from datetime import datetime, timedelta, timezone
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from translator_bot.maintenance_dispatch import dispatch_decision

NOW = datetime(2026, 10, 19, 20, tzinfo=timezone.utc)
SPEC = importlib.util.spec_from_file_location('maintenance_runner', Path(__file__).resolve().parents[1]/'scripts'/'run_maintenance.py')
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class DispatchTests(unittest.TestCase):
    def queue(self, due=True, ident=1):
        return {'due':due,'eligible_posts':3 if due else 0,
                'groups':[{'report_ids':[ident],'fingerprints':{str(ident):'abc'}}] if due else []}

    def test_idle_never_launches(self):
        self.assertFalse(dispatch_decision(self.queue(False),{},now=NOW)['launch'])

    def test_due_starts_once_but_changed_job_is_eligible(self):
        queue=self.queue(); initial=dispatch_decision(queue,{},now=NOW)
        self.assertTrue(initial['launch'])
        state={'jobs':{initial['signature']:{'exit_code':0,'completion_verified':True}}}
        self.assertFalse(dispatch_decision(queue,state,now=NOW)['launch'])
        self.assertTrue(dispatch_decision(self.queue(ident=2),state,now=NOW)['launch'])

    def test_failure_backoff_and_limit(self):
        queue=self.queue(); key=dispatch_decision(queue,{},now=NOW)['signature']
        job={'started_at':(NOW-timedelta(minutes=10)).isoformat(),'attempts':1,'exit_code':1}
        state={'jobs':{key:job}}
        self.assertEqual(dispatch_decision(queue,state,now=NOW)['reason'],'failure_backoff')
        self.assertTrue(dispatch_decision(queue,state,now=NOW+timedelta(minutes=30))['launch'])
        job['attempts']=3
        self.assertEqual(dispatch_decision(queue,state,now=NOW+timedelta(days=1))['reason'],'retry_limit_needs_attention')
        self.assertTrue(dispatch_decision(queue,state,now=NOW,force=True)['launch'])

    def test_health_alert_only_not_healthy_daily_check_triggers_ai(self):
        self.assertTrue(dispatch_decision(self.queue(False),{},alerts=['Cache integrity failed'],now=NOW)['launch'])
        self.assertFalse(dispatch_decision(self.queue(False),{},alerts=[],now=NOW)['launch'])

    def test_legacy_zero_exit_without_receipts_is_not_permanent_success(self):
        queue=self.queue();key=dispatch_decision(queue,{},now=NOW)['signature']
        state={'jobs':{key:{'exit_code':0,'attempts':1,'started_at':(NOW-timedelta(hours=1)).isoformat()}}}
        self.assertTrue(dispatch_decision(queue,state,now=NOW)['launch'])
        state['jobs'][key]['started_at']=NOW.isoformat()
        self.assertEqual(dispatch_decision(queue,state,now=NOW)['reason'],'unverified_legacy_job')

    def fixture(self, root, count):
        database=root/'settings.sqlite3'
        with closing(sqlite3.connect(database)) as db, db:
            db.execute('CREATE TABLE translation_feedback (id INTEGER, guild_id INTEGER, channel_id INTEGER, translation_message_id INTEGER, source_text TEXT, translated_text TEXT, corrected_text TEXT, status TEXT, created_at TEXT)')
            for ident in range(count):
                db.execute('INSERT INTO translation_feedback VALUES (?,?,?,?,?,?,?,?,?)',
                           (ident+1,1,2,ident+5,'source','translation',None,'open',datetime.now(timezone.utc).replace(tzinfo=None).isoformat()))
        from translator_bot.result_cache import ResultCache
        ResultCache(database.with_suffix('.cache.sqlite3'))
        return SimpleNamespace(database_path=database,guild_ids=(1,))

    def test_real_idle_preflight_never_starts_subprocess_or_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);settings=self.fixture(root,0)
            with patch.object(RUNNER,'DATA',root/'maintenance'), patch.object(RUNNER.Settings,'load',return_value=settings), \
                    patch.object(RUNNER,'health_check',return_value=[]), patch.object(RUNNER.subprocess,'run') as start, \
                    patch.object(RUNNER.sys,'argv',['run_maintenance.py']):
                RUNNER.main()
                start.assert_not_called()
            self.assertEqual(json.loads((root/'maintenance'/'dispatcher_status.json').read_text())['reason'],'nothing_due')

    def test_real_due_preflight_launches_bounded_fresh_reviewer_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);settings=self.fixture(root,3)
            binary=root/'codex.exe';binary.touch()
            with patch.object(RUNNER,'DATA',root/'maintenance'), patch.object(RUNNER.Settings,'load',return_value=settings), \
                    patch.object(RUNNER,'health_check',return_value=[]), \
                    patch.object(RUNNER.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as start, \
                    patch.object(RUNNER,'reviewer_completion',return_value=(True,'verified test receipts')), \
                    patch.object(RUNNER.sys,'argv',['run_maintenance.py','--codex',str(binary)]):
                RUNNER.main();RUNNER.main()
                start.assert_called_once()
                command=start.call_args.args[0]
                self.assertIn('--ephemeral',command)
                self.assertIn('workspace-write',command)
                self.assertNotIn('--disable',command)
                self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',command)
                self.assertNotIn('--model',command)
                self.assertNotIn('resume',command)

    def test_zero_exit_with_failed_commands_is_a_failed_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);settings=self.fixture(root,3)
            binary=root/'codex.exe';binary.touch()
            with patch.object(RUNNER,'DATA',root/'maintenance'),patch.object(RUNNER.Settings,'load',return_value=settings), \
                    patch.object(RUNNER,'health_check',return_value=[]), \
                    patch.object(RUNNER.subprocess,'run',return_value=SimpleNamespace(returncode=0)), \
                    patch.object(RUNNER.sys,'argv',['run_maintenance.py','--codex',str(binary)]):
                with self.assertRaises(RuntimeError):RUNNER.main()
            job=next(iter(json.loads((root/'maintenance'/'dispatcher_state.json').read_text())['jobs'].values()))
            self.assertEqual(job['exit_code'],1)
            self.assertEqual(job['codex_exit_code'],0)
            self.assertFalse(job['completion_verified'])
            runtime=json.loads((root/'maintenance'/'reviewer_runtime.json').read_text())
            self.assertEqual(runtime['status'],'failed')
            self.assertFalse(runtime['completion_verified'])

    def test_missing_runtime_is_visible_without_consuming_a_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);settings=self.fixture(root,3)
            with patch.object(RUNNER,'DATA',root/'maintenance'),patch.object(RUNNER.Settings,'load',return_value=settings), \
                    patch.object(RUNNER,'health_check',return_value=[]),patch.object(RUNNER,'resolve_codex',return_value=None), \
                    patch.object(RUNNER.sys,'argv',['run_maintenance.py']),patch.object(RUNNER.subprocess,'run') as start:
                with self.assertRaisesRegex(RuntimeError,'executable unavailable'):RUNNER.main()
                start.assert_not_called()
            self.assertFalse((root/'maintenance'/'dispatcher_state.json').exists())
            status=json.loads((root/'maintenance'/'dispatcher_status.json').read_text())
            self.assertFalse(status['launch']);self.assertEqual(status['reason'],'reviewer_executable_unavailable')
            self.assertEqual(json.loads((root/'maintenance'/'reviewer_runtime.json').read_text())['status'],'failed')

    def test_process_start_failure_has_durable_failed_job_and_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);settings=self.fixture(root,3);binary=root/'codex.exe';binary.touch()
            with patch.object(RUNNER,'DATA',root/'maintenance'),patch.object(RUNNER.Settings,'load',return_value=settings), \
                    patch.object(RUNNER,'health_check',return_value=[]), \
                    patch.object(RUNNER.subprocess,'run',side_effect=OSError('test start failure')), \
                    patch.object(RUNNER.sys,'argv',['run_maintenance.py','--codex',str(binary)]):
                with self.assertRaisesRegex(RuntimeError,'could not start'):RUNNER.main()
            runtime=json.loads((root/'maintenance'/'reviewer_runtime.json').read_text())
            self.assertEqual(runtime['reason'],'reviewer_process_start_failed')
            self.assertFalse(runtime['completion_verified'])
            job=next(iter(json.loads((root/'maintenance'/'dispatcher_state.json').read_text())['jobs'].values()))
            self.assertEqual(job['exit_code'],1)

    def test_session_zero_idle_never_launches_interactive_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);settings=self.fixture(root,0)
            with patch.object(RUNNER,'DATA',root/'maintenance'),patch.object(RUNNER.Settings,'load',return_value=settings), \
                    patch.object(RUNNER,'health_check',return_value=[]),patch.object(RUNNER,'session_zero',return_value=True), \
                    patch.object(RUNNER.subprocess,'run') as start,patch.object(RUNNER.sys,'argv',['run_maintenance.py']):
                RUNNER.main();start.assert_not_called()

    def test_session_zero_due_requests_worker_without_marking_review_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);settings=self.fixture(root,3)
            with patch.object(RUNNER,'DATA',root/'maintenance'),patch.object(RUNNER.Settings,'load',return_value=settings), \
                    patch.object(RUNNER,'health_check',return_value=[]),patch.object(RUNNER,'session_zero',return_value=True), \
                    patch.object(RUNNER.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as start, \
                    patch.object(RUNNER.sys,'argv',['run_maintenance.py']):
                RUNNER.main()
            self.assertIn('Discord Translator Maintenance Reviewer',start.call_args.args[0])
            self.assertNotIn('exec',start.call_args.args[0])
            self.assertFalse((root/'maintenance'/'dispatcher_state.json').exists())
            self.assertFalse(json.loads((root/'maintenance'/'dispatcher_status.json').read_text())['launch'])
            self.assertEqual(json.loads((root/'maintenance'/'handoff_status.json').read_text())['request_exit_code'],0)

    def test_no_signed_in_worker_does_not_count_as_completed_or_consume_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);settings=self.fixture(root,3)
            with patch.object(RUNNER,'DATA',root/'maintenance'),patch.object(RUNNER.Settings,'load',return_value=settings), \
                    patch.object(RUNNER,'health_check',return_value=[]),patch.object(RUNNER,'session_zero',return_value=True), \
                    patch.object(RUNNER.subprocess,'run',return_value=SimpleNamespace(returncode=1)), \
                    patch.object(RUNNER.sys,'argv',['run_maintenance.py']):
                RUNNER.main()
            self.assertFalse((root/'maintenance'/'dispatcher_state.json').exists())
            self.assertEqual(json.loads((root/'maintenance'/'handoff_status.json').read_text())['reason'],'reviewer_not_started')

    def test_interactive_worker_refuses_misconfigured_session_zero_principal(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(RUNNER,'DATA',Path(temporary)), \
                patch.object(RUNNER,'session_zero',return_value=True), \
                patch.object(RUNNER.sys,'argv',['run_maintenance.py','--interactive-reviewer']), \
                patch.object(RUNNER.subprocess,'run') as start:
            with self.assertRaises(RuntimeError):RUNNER.main()
            start.assert_not_called()

    def test_completion_requires_commands_and_fresh_matching_receipts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);queue=self.queue()
            path=root/'events.jsonl'
            event={'type':'item.completed','item':{'type':'command_execution','status':'completed','exit_code':0}}
            path.write_text(json.dumps(event)+'\n',encoding='utf-8')
            receipt={'1':{'fingerprint':'abc','outcome':'needs_clarification','attempted_at':NOW.isoformat()}}
            self.assertTrue(RUNNER.reviewer_completion(root,queue,receipt,NOW)[0])
            health_only=dict(queue,due=False)
            self.assertTrue(RUNNER.reviewer_completion(root,health_only,{},NOW)[0])
            self.assertFalse(RUNNER.reviewer_completion(root,queue,{},NOW)[0])
            receipt['1']['fingerprint']='stale'
            self.assertFalse(RUNNER.reviewer_completion(root,queue,receipt,NOW)[0])
            receipt['1'].update(fingerprint='abc',attempted_at=(NOW-timedelta(seconds=1)).isoformat())
            self.assertFalse(RUNNER.reviewer_completion(root,queue,receipt,NOW)[0])
            event['item'].update(status='failed',exit_code=-1)
            path.write_text(json.dumps(event)+'\n',encoding='utf-8')
            self.assertFalse(RUNNER.reviewer_completion(root,queue,receipt,NOW)[0])

    def test_dry_run_never_launches_even_when_due(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);settings=self.fixture(root,3)
            with patch.object(RUNNER,'DATA',root/'maintenance'), patch.object(RUNNER.Settings,'load',return_value=settings), \
                    patch.object(RUNNER,'health_check',return_value=[]), patch.object(RUNNER.subprocess,'run') as start, \
                    patch.object(RUNNER.sys,'argv',['run_maintenance.py','--dry-run']):
                RUNNER.main();start.assert_not_called()

    def test_lock_prevents_overlap(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'lock'
            with RUNNER.dispatch_lock(path) as first:
                self.assertTrue(first)
                with RUNNER.dispatch_lock(path) as second:self.assertFalse(second)
            with RUNNER.dispatch_lock(path) as later:self.assertTrue(later)

    def test_fresh_health_receipt_skips_repeated_database_and_log_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            data=Path(temporary)
            (data/'health_state.json').write_text(json.dumps({'checked_at':NOW.isoformat(),'alerts':[]}))
            with patch.object(RUNNER,'DATA',data), patch.object(RUNNER.sqlite3,'connect') as connect:
                self.assertEqual(RUNNER.health_check(SimpleNamespace(),NOW+timedelta(hours=1)),[])
                connect.assert_not_called()

    def test_healthy_daily_check_is_local_and_failure_alert_is_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);data=root/'maintenance';data.mkdir()
            logs=root/'logs';logs.mkdir()
            settings=SimpleNamespace(database_path=root/'settings.sqlite3')
            with closing(sqlite3.connect(settings.database_path.with_suffix('.cache.sqlite3'))) as db, db:
                db.execute('CREATE TABLE cache_revision (id INTEGER, revision INTEGER)')
                db.execute('INSERT INTO cache_revision VALUES (1,0)')
            (logs/'bot.log').write_text('logging in using static token\nDelivery metrics guild=1 outcome=delivered fast_cache_hit=True total=1.500s queue=0.000s\n')
            with patch.object(RUNNER,'DATA',data),patch.object(RUNNER,'ROOT',root):
                self.assertEqual(RUNNER.health_check(settings,NOW),[])
                record=json.loads((data/'health_state.json').read_text())
                self.assertEqual(record['cache_integrity'],'ok')
                self.assertEqual(record['actual_fast_cache_hits'],1)
                self.assertEqual(record['average_delivered_seconds'],1.5)
                (logs/'bot.log').write_text('logging in using static token\n'+
                    'Delivery metrics guild=1 outcome=failed fast_cache_hit=False total=2.000s queue=0.000s\n'*3)
                self.assertEqual(len(RUNNER.health_check(settings,NOW+timedelta(days=1))),1)

    def test_missing_cache_and_bot_log_produce_bounded_health_alerts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);data=root/'maintenance';data.mkdir()
            settings=SimpleNamespace(database_path=root/'missing.sqlite3')
            with patch.object(RUNNER,'DATA',data),patch.object(RUNNER,'ROOT',root):
                alerts=RUNNER.health_check(settings,NOW)
                self.assertEqual(len(alerts),2)
                self.assertIn('Cache integrity',alerts[1])
