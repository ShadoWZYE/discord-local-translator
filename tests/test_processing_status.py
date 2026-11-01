import asyncio
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from translator_bot.bot import create_bot
from translator_bot.config import Settings
from translator_bot.priority import PrioritySlot
from translator_bot.processing_metrics import ProcessingMetrics


class ProcessingMetricsTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.metrics = ProcessingMetrics(clock=lambda: self.now)

    def test_success_only_averages_percentiles_and_guild_isolation(self):
        self.metrics.record(1,'delivered',total=2,queue=1,delivery=.3,cache_hit=True)
        self.metrics.record(1,'delivered',total=4,queue=0,delivery=.1)
        self.metrics.record(1,'failed',total=100)
        self.metrics.record(1,'missing',total=200)
        self.metrics.record(2,'delivered',total=1000)
        result = self.metrics.summary(1)
        self.assertEqual((result['total'],result['median'],result['p95']), (3,3,4))
        self.assertEqual((result['failed'],result['missing'],result['cache_hits']), (1,1,1))
        self.assertAlmostEqual(result['delivery'], .2)

    def test_empty_sample_has_no_average_and_old_requests_do_not_expire(self):
        self.assertIsNone(self.metrics.summary(1)['total'])
        self.metrics.record(1,'delivered',total=2)
        self.now = 86400
        result = self.metrics.summary(1)
        self.assertEqual(result['delivered'],1)
        self.assertEqual(result['p95'],2)
        self.assertEqual(result['samples'],1)

    def test_storage_is_bounded_and_retains_no_source_text(self):
        metrics = ProcessingMetrics(limit=2)
        for value in (1,2,3):
            metrics.record(1,'delivered',total=value)
        self.assertEqual(len(metrics.samples[1]),2)
        self.assertEqual(metrics.summary(1)['total'],2.5)
        self.assertNotIn('text',metrics.samples[1][0])

    def test_last_fifty_are_retained_independently_per_server(self):
        self.metrics.record(2,'delivered',total=10)
        for value in range(1,61):
            self.metrics.record(1,'delivered',total=value)
        result = self.metrics.summary(1)
        self.assertEqual(result['samples'],50)
        self.assertEqual(result['total'],35.5)
        self.assertEqual(result['p95'],58)
        self.assertEqual(self.metrics.summary(2)['samples'],1)
        self.assertEqual(self.metrics.summary(2)['total'],10)

    def test_partial_reuse_is_live_delivery_data_not_a_full_hit_or_time_estimate(self):
        self.metrics.record(1,'delivered',total=5,reused_characters=20,total_characters=80)
        self.metrics.record(1,'failed',total=30,reused_characters=50,total_characters=80)
        self.metrics.record(1,'delivered',total=.4,cache_hit=True,reused_characters=30,total_characters=30)
        result=self.metrics.summary(1)
        self.assertEqual(result['cache_hits'],1)
        self.assertEqual(result['partial_cache_deliveries'],1)
        self.assertEqual(result['reused_characters'],50)
        self.assertEqual(result['total_characters'],110)

    def test_disk_samples_survive_restart_with_wall_clock_and_guild_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'metrics.db'
            first=ProcessingMetrics(path=path,limit=2,clock=lambda:100)
            first.record(2,'delivered',total=10)
            for total in (1,2,3):first.record(1,'delivered',total=total)
            second=ProcessingMetrics(path=path,limit=2,clock=lambda:200)
            self.assertEqual(second.summary(1)['samples'],2)
            self.assertEqual(second.summary(1)['total'],2.5)
            self.assertEqual(second.summary(1)['sample_span_seconds'],100)
            self.assertEqual(second.summary(2)['total'],10)
            second.record(1,'failed',total=99)
            third=ProcessingMetrics(path=path,limit=2)
            self.assertEqual(third.summary(1)['failed'],1)
            self.assertEqual(third.summary(1)['total'],3)

    def test_cumulative_counters_do_not_double_count_status_queries_or_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'metrics.db'
            first=ProcessingMetrics(path=path)
            self.assertEqual(first.host_counters({'lookup_hits':3})['lookup_hits'],3)
            self.assertEqual(first.host_counters({'lookup_hits':3})['lookup_hits'],3)
            self.assertEqual(first.host_counters({'lookup_hits':5})['lookup_hits'],5)
            second=ProcessingMetrics(path=path)
            self.assertEqual(second.host_counters({'lookup_hits':2})['lookup_hits'],7)
            self.assertEqual(first.host_counters({'lookup_hits':6})['lookup_hits'],8)

    def test_unreadable_optional_database_preserves_file_and_keeps_memory_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'metrics.db'
            path.write_bytes(b'synthetic invalid sqlite file')
            before=path.read_bytes()
            metrics=ProcessingMetrics(path=path)
            metrics.record(1,'delivered',total=2)
            self.assertEqual(metrics.summary(1)['total'],2)
            self.assertEqual(metrics.host_counters({'lookup_hits':3}),{'lookup_hits':3})
            self.assertFalse(metrics.history_persisted)
            self.assertFalse(metrics.counters_persisted)
            self.assertEqual(path.read_bytes(),before)

    def test_invalid_saved_sample_does_not_hide_valid_history_or_delete_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'metrics.db'
            metrics=ProcessingMetrics(path=path)
            metrics.record(1,'delivered',total=3)
            with closing(sqlite3.connect(path)) as db,db:
                db.execute('INSERT INTO deliveries(guild,payload) VALUES(1,?)',('{invalid json',))
                db.execute('INSERT INTO deliveries(guild,payload) VALUES(1,?)',('{"guild":1,"outcome":"delivered","total":"bad"}',))
            restarted=ProcessingMetrics(path=path)
            self.assertEqual(restarted.summary(1)['total'],3)
            self.assertEqual(restarted.summary(1)['samples'],1)
            self.assertFalse(restarted.history_persisted)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0],3)

    def test_storage_outage_is_visible_and_counters_recover_without_double_counting(self):
        with tempfile.TemporaryDirectory() as directory:
            metrics=ProcessingMetrics(path=Path(directory)/'metrics.db')
            self.assertEqual(metrics.host_counters({'lookup_hits':3}),{'lookup_hits':3})
            with patch.object(metrics,'_connect',side_effect=sqlite3.OperationalError('synthetic locked storage')):
                metrics.record(1,'delivered',total=2)
                self.assertEqual(metrics.host_counters({'lookup_hits':4}),{'lookup_hits':4})
                self.assertFalse(metrics.history_persisted)
                self.assertFalse(metrics.counters_persisted)
            self.assertEqual(metrics.host_counters({'lookup_hits':5}),{'lookup_hits':5})
            self.assertTrue(metrics.counters_persisted)
            self.assertEqual(metrics.summary(1)['samples'],1)

    def test_corrupt_counter_metadata_falls_back_without_overwriting_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'metrics.db';metrics=ProcessingMetrics(path=path)
            with closing(sqlite3.connect(path)) as db,db:
                db.execute('INSERT INTO counter_sessions VALUES(?,?)',(metrics.session,'not json'))
            self.assertEqual(metrics.host_counters({'lookup_hits':2}),{'lookup_hits':2})
            self.assertFalse(metrics.counters_persisted)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT payload FROM counter_sessions').fetchone()[0],'not json')


class ProcessingStatusCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bot = create_bot(Settings('test',1,Path(self.temp.name)/'db','INFO','http://localhost','test',90))
        self.interaction = SimpleNamespace(guild_id=1,response=SimpleNamespace(send_message=AsyncMock()))
        self.command = self.bot.tree.get_command('tr-status')

    async def asyncTearDown(self):
        await self.bot.close()
        self.temp.cleanup()

    async def test_command_exists_and_empty_status_is_ephemeral(self):
        self.assertIsNotNone(self.command)
        await self.command.callback(self.interaction)
        kwargs = self.interaction.response.send_message.call_args.kwargs
        self.assertTrue(kwargs['ephemeral'])
        text = str(kwargs['embed'].to_dict())
        self.assertIn('no average available',text)
        self.assertIn('retained across restarts',text)
        self.assertIn('0/50',text)
        self.assertIn('no time cutoff',text)

    async def test_status_reports_this_server_not_other_servers(self):
        self.bot.processing_metrics.record(1,'delivered',total=3.5,translation=2,delivery=.2)
        self.bot.processing_metrics.record(2,'delivered',total=999)
        await self.command.callback(self.interaction)
        text = str(self.interaction.response.send_message.call_args.kwargs['embed'].to_dict())
        self.assertIn('3.50s',text)
        self.assertIn('1 completed batches',text)
        self.assertNotIn('999',text)
        self.assertIn('all servers',text)

    async def test_unallowed_guild_receives_no_metrics(self):
        self.interaction.guild_id = 999
        await self.command.callback(self.interaction)
        self.assertNotIn('embed',self.interaction.response.send_message.call_args.kwargs)
        self.assertTrue(self.interaction.response.send_message.call_args.kwargs['ephemeral'])

    async def test_status_fallback_does_not_claim_process_counters_are_persisted(self):
        self.bot.processing_metrics=ProcessingMetrics()
        await self.command.callback(self.interaction)
        text=str(self.interaction.response.send_message.call_args.kwargs['embed'].to_dict())
        self.assertIn('current process only',text)
        self.assertIn('Status persistence warning',text)
        self.assertNotIn('retained across restarts',text)
        self.assertNotIn('persisted totals',text)

    async def test_gpu_waiter_count_ignores_cancellations(self):
        gate = PrioritySlot()
        await gate.acquire()
        waiter = asyncio.create_task(gate.acquire())
        await asyncio.sleep(0)
        self.assertEqual(gate.waiting_count(),1)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual(gate.waiting_count(),0)
        gate.release()
