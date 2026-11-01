from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from translator_bot.result_cache import ResultCache


class CacheStorageCostTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.cache=ResultCache(Path(temp.name)/'cache.db',limit=2,ttl=1000,touch_interval=60,auto_trim=True)

    def test_frequent_hits_do_not_write_or_extend_ttl(self):
        cache=self.cache
        with patch('translator_bot.result_cache.time.time',return_value=1000):
            self.assertTrue(cache.put(b'key','value',(0,0),b'source'))
        statements=[];connect=cache._connect
        def traced():
            db=connect();db.set_trace_callback(statements.append);return db
        with patch.object(cache,'_connect',side_effect=traced),patch('translator_bot.result_cache.time.time',return_value=1001):
            for _ in range(3):self.assertEqual(cache.get(b'key',b'source')[0],'value')
        self.assertFalse(any(s.startswith('UPDATE') for s in statements))
        with patch('translator_bot.result_cache.time.time',return_value=1061):cache.get(b'key',b'source')
        with closing(connect()) as db:
            self.assertEqual(db.execute('SELECT expires,accessed FROM results').fetchone(),(2000,1061))
        with patch('translator_bot.result_cache.time.time',return_value=2001):
            self.assertIsNone(cache.get(b'key',b'source')[0])

    def test_fresh_snapshot_sees_other_writer_invalidation(self):
        cache=self.cache;cache.put(b'key','value',(0,0),b'source')
        other=ResultCache(cache.path)
        self.assertEqual(cache.get(b'key',b'source')[0],'value')
        other.invalidate(b'source')
        self.assertEqual(cache.get(b'key',b'source'),(None,(0,1)))
        self.assertFalse(cache.put(b'key','stale',(0,0),b'source'))

    def test_old_touch_does_not_wait_for_writer_or_turn_hit_into_failure(self):
        cache=self.cache;cache.put(b'key','value',(0,0),b'source')
        with closing(cache._connect()) as db,db:db.execute('UPDATE results SET accessed=0')
        with closing(cache._connect()) as writer:
            writer.execute('BEGIN IMMEDIATE')
            self.assertEqual(cache.get(b'key',b'source')[0],'value')
            writer.rollback()

    def test_retention_is_bounded_and_joint_observation_uses_one_connection(self):
        cache=self.cache
        for i in range(4):cache.put(bytes([i]),'value',(0,0),b'source')
        self.assertEqual(cache.stored_count(),2)
        connect=cache._connect
        with patch.object(cache,'_connect',wraps=connect) as calls:
            cache.observe_fragment(b'unit',b'identity',hit=False,eligible=True,reason='test',characters=4,
                request=(b'message',b'version','automatic'))
            self.assertEqual(calls.call_count,1)
        with closing(connect()) as db:
            self.assertEqual(db.execute('SELECT lookups,misses FROM fragment_observations').fetchone(),(1,1))
            self.assertEqual(db.execute('SELECT origin,attempts FROM request_fragment_observations').fetchone(),('automatic',1))

    def test_indexes_do_not_drop_retained_entries(self):
        cache=self.cache;cache.put(b'key','value',(0,0),b'source')
        restarted=ResultCache(cache.path)
        self.assertEqual(restarted.get(b'key',b'source')[0],'value')
        with closing(cache._connect()) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_batched_approval_reads_keep_scope_priority_and_fresh_revisions(self):
        cache=self.cache
        cache.approve_memory(b'a',b'source','first','portable','{}',(0,0))
        cache.approve_memory(b'b',b'other','second','portable','{}',(0,0))
        requests=[([b'a',b'b'],b'source'),([b'b'],b'other'),([b'a'],b'wrong')]
        with patch.object(cache,'_connect',wraps=cache._connect) as calls:
            self.assertEqual(cache.memory_get_many(requests),['first','second',None])
            self.assertEqual(calls.call_count,1)
        ResultCache(cache.path).invalidate(b'source')
        self.assertEqual(cache.memory_get_many(requests),[None,'second',None])
        self.assertEqual(cache.memory_get_many([([],b'source')]),[None])
