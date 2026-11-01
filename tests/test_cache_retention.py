from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from translator_bot.result_cache import ResultCache, capacity_status
from scripts import run_maintenance


class PersistentRetentionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'cache.sqlite3'

    def test_clock_jump_restart_and_legacy_expiry_preserve_results(self):
        cache=ResultCache(self.path,limit=1)
        cache.put(b'old','known',(0,0),b'source',reviewed=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE results SET expires=1')
        with patch('translator_bot.result_cache.time.time',return_value=10**12):
            cache=ResultCache(self.path,limit=1)
            self.assertEqual(cache.get(b'old',b'source')[0],'known')
            self.assertEqual(cache.reviewed_count(),1)
            self.assertEqual(cache.miss_reason(b'new',b'source'),'other_key')
            cache.put(b'new','another',(0,0),b'other')
            self.assertEqual(cache.stored_count(),2)
            self.assertEqual(cache.get(b'old',b'source')[0],'known')
            self.assertFalse(cache.put(b'old','unreviewed',(0,0),b'source'))
        cache.invalidate(b'source')
        self.assertIsNone(cache.get(b'old',b'source')[0])
        self.assertEqual(cache.get(b'new',b'other')[0],'another')
        self.assertFalse(cache.put(b'old','stale',(0,0),b'source'))

    def test_capacity_thresholds_do_not_evict(self):
        cache=ResultCache(self.path,limit=10)
        for count in range(1,12):
            cache.put(bytes([count]),'known',0)
            with closing(sqlite3.connect(self.path)) as db:
                status=capacity_status(db,10)
            expected='over_capacity' if count>=10 else 'high' if count>=9 else 'warning' if count>=8 else 'normal'
            self.assertEqual(status['pressure'],expected)
            self.assertEqual(status['retained_results'],count)
            self.assertGreater(status['database_bytes'],0)

    def test_preflight_is_readonly_and_alert_is_stable(self):
        # Settings cache-path convention uses .cache.sqlite3.
        settings=SimpleNamespace(database_path=self.path.parent/'cache.sqlite3')
        expected=settings.database_path.with_suffix('.cache.sqlite3')
        cache=ResultCache(expected)
        now=datetime.now(timezone.utc)
        with patch.object(run_maintenance,'DATA',self.path.parent), patch.object(run_maintenance,'capacity_status',side_effect=lambda db:capacity_status(db,10)):
            self.assertEqual(run_maintenance.capacity_check(settings,now),[])
            for n in range(8): cache.put(bytes([n]),'known',0)
            first=run_maintenance.capacity_check(settings,now)
            self.assertEqual(len(first),1)
            cache.put(b'next','known',0)
            self.assertNotEqual(first,run_maintenance.capacity_check(settings,now))
            cache.put(b'full','known',0)
            full=run_maintenance.capacity_check(settings,now)
            cache.put(b'overflow','known',0)
            self.assertEqual(full,run_maintenance.capacity_check(settings,now))
            self.assertEqual(cache.stored_count(),11)
