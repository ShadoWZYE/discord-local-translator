import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from translator_bot.config import Settings
from translator_bot.result_cache import ResultCache
from translator_bot.translation import LocalTranslator, TranslationResult, TranslationUnavailable


class PersistentCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'results.sqlite3'

    def translator(self):
        t = LocalTranslator('http://localhost:11434', cache_path=self.path)
        t._translate_uncached = Mock(return_value=TranslationResult('en', 'ru', 'Привет'))
        return t

    def test_restart_and_code_patch_preserve_cache_until_source_invalidation(self):
        t = self.translator()
        t.translate_to('Hello', 'ru', 'en')
        other = self.translator()
        self.assertEqual(other.translate_to('Hello', 'ru', 'en').text, 'Привет')
        other._translate_uncached.assert_not_called()
        with other.conversation_context('different reply'):
            other.translate_to('Hello', 'ru', 'en')
        other._translate_uncached.assert_called_once()
        other._policy_code = b'changed rules'
        other.translate_to('Hello', 'ru', 'en')
        self.assertEqual(other._translate_uncached.call_count, 1)
        other.invalidate_source('Hello')
        other.translate_to('Hello', 'ru', 'en')
        self.assertEqual(other._translate_uncached.call_count, 2)

    def test_cross_process_invalidation_clears_memory_and_blocks_old_write(self):
        t = self.translator()
        t.translate_to('Hello', 'ru', 'en')
        cache = ResultCache(self.path)
        _, revision = cache.get(b'other')
        cache.clear()
        cache.put(b'other', 'stale', revision)
        self.assertIsNone(cache.get(b'other')[0])
        t.translate_to('Hello', 'ru', 'en')
        self.assertEqual(t._translate_uncached.call_count, 2)
        t.clear_cache()
        third = self.translator()
        third.translate_to('Hello', 'ru', 'en')
        third._translate_uncached.assert_called_once()

    def test_no_failures_or_invalidated_inflight_results_are_persisted(self):
        t = self.translator()
        t._translate_uncached.side_effect = TranslationUnavailable('offline')
        with self.assertRaises(TranslationUnavailable):
            t.translate_to('Hello', 'ru', 'en')
        def generate(*args):
            t.clear_cache()
            return TranslationResult('en', 'ru', 'Привет')
        t._translate_uncached.side_effect = generate
        t.translate_to('Hello', 'ru', 'en')
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM results').fetchone()[0], 0)

    def test_expiry_lru_and_no_source_plaintext(self):
        cache = ResultCache(self.path, limit=2, ttl=30, auto_trim=True)
        for key in (b'a', b'b', b'c'):
            cache.put(key, 'translation', 0)
            time.sleep(0.002)
        self.assertIsNone(cache.get(b'a')[0])
        self.assertEqual(cache.get(b'b')[0], 'translation')
        cache.put(b'd', 'translation', 0)
        self.assertIsNone(cache.get(b'c')[0])
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE results SET expires=0')
        self.assertIsNone(cache.get(b'b')[0])
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual([row[1] for row in db.execute('PRAGMA table_info(results)')],
                ['key', 'value', 'expires', 'accessed', 'source', 'reviewed'])

    def test_targeted_invalidation_preserves_unrelated_results_across_processes(self):
        t = self.translator()
        t.translate_to('Hello', 'ru', 'en')
        t.translate_to('Goodbye', 'ru', 'en')
        other = self.translator()
        other.invalidate_source('Hello')
        t.translate_to('Goodbye', 'ru', 'en')
        self.assertEqual(t._translate_uncached.call_count, 2)
        t.translate_to('Hello', 'ru', 'en')
        self.assertEqual(t._translate_uncached.call_count, 3)
        self.assertIsNotNone(other.cached_translation('Goodbye', 'en', 'ru'))

    def test_targeted_invalidation_blocks_inflight_write_only_for_affected_text(self):
        t = self.translator()
        t.translate_to('Goodbye', 'ru', 'en')
        def generate(*args):
            t.invalidate_source('Hello')
            return TranslationResult('en', 'ru', 'Привет')
        t._translate_uncached.side_effect = generate
        t.translate_to('Hello', 'ru', 'en')
        self.assertIsNone(t.cached_translation('Hello', 'en', 'ru'))
        self.assertIsNotNone(t.cached_translation('Goodbye', 'en', 'ru'))

    def test_all_contexts_and_targets_invalidated_not_other_source(self):
        t = self.translator()
        t.translate_to('Hello', 'ru', 'en')
        with t.conversation_context('another conversation'):
            t.translate_to('Hello', 'es', 'en')
        t.invalidate_source('Hello')
        self.assertIsNone(t.cached_translation('Hello', 'en', 'ru'))
        with t.conversation_context('another conversation'):
            self.assertIsNone(t.cached_translation('Hello', 'en', 'es'))

    def test_legacy_database_migration_preserves_rows(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE results(key BLOB PRIMARY KEY,value TEXT,expires REAL,accessed REAL)')
            db.execute('INSERT INTO results VALUES(?,?,?,?)', (b'key','translation',time.time()+60,time.time()))
        cache = ResultCache(self.path)
        self.assertEqual(cache.get(b'key')[0], 'translation')

    def test_stats_distinguish_fast_lookups_from_generation_cache(self):
        t = self.translator()
        self.assertIsNone(t.cached_translation('Hello', 'en', 'ru'))
        t.translate_to('Hello', 'ru', 'en')
        t.translate_to('Hello', 'ru', 'en')
        self.assertIsNotNone(t.cached_translation('Hello', 'en', 'ru'))
        stats = t.cache_stats()
        self.assertEqual((stats['misses'],stats['memory_hits'],stats['lookup_hits'],stats['lookup_misses']),
            (1,1,1,1))

    def test_unavailable_database_does_not_break_translation(self):
        t = self.translator()
        with patch.object(t._disk_cache, 'get', side_effect=sqlite3.OperationalError('busy')):
            self.assertEqual(t.translate_to('Hello', 'ru', 'en').text, 'Привет')


class SpeedSafetyTests(unittest.TestCase):
    def test_shared_prefix_precedes_all_dynamic_metadata(self):
        t = LocalTranslator('http://localhost:11434')
        a = t._prompt('Hello\n[[DCTOKEN_0]]', 'en', 'ru')
        b = t._prompt('Привет.', 'ru', 'en')
        self.assertEqual(a.split('Translate from ')[0], b.split('Translate from ')[0])
        self.assertGreater(len(a.split('Translate from ')[0]), 1800)

    def test_fixed_only_layout_never_generates(self):
        t = LocalTranslator('http://localhost:11434')
        t._request = Mock()
        self.assertEqual(t._translate_layout('@here\n\n123', 'en', 'ru'), '@here\n\n123')
        t._request.assert_not_called()

    def test_fixed_fields_are_not_output_keys_and_cannot_leak(self):
        t = LocalTranslator('http://localhost:11434')
        t._request = Mock(return_value=json.dumps({'part_1': 'I am going home.'}))
        self.assertEqual(t._translate_layout('Хаха\nЯ иду домой.\n\n@here', 'ru', 'en'),
            'Haha\nI am going home.\n\n@here')
        self.assertEqual(t._request.call_args.kwargs['format_schema']['required'], ['part_1'])
        with self.assertRaises(TranslationUnavailable):
            t._decode_layout('{"part_1":"Haha, I am going home."}',
                {'part_0':'Хаха', 'part_1':'Я иду домой.'}, {'part_0':'Haha'}, {})

    def test_syntax_fallback_omits_fixed_fields_and_restores_them(self):
        t = LocalTranslator('http://localhost:11434')
        t._request = Mock(return_value=json.dumps({'segment_0':'Привет', 'segment_4':'пока'}))
        # Inspect dynamic key allocation rather than relying on token spelling.
        def generate(prompt, *, format_schema):
            keys = format_schema['required']
            self.assertEqual(len(keys), 2)
            return json.dumps(dict(zip(keys, ['Привет', 'пока'])))
        t._request.side_effect = generate
        self.assertEqual(t._translate_segmented_layout('Hello @here\nbye', 'en', 'ru'),
            'Привет @here\nпока')

    def test_request_uses_configured_batch_and_defaults_shorten_pause(self):
        t = LocalTranslator('http://localhost:11434', num_batch=256)
        response = Mock()
        response.json.return_value = {'message': {'content': 'Привет'}}
        t._session.post = Mock(return_value=response)
        t._request('Hello')
        self.assertEqual(t._session.post.call_args.kwargs['json']['options']['num_batch'], 256)
        s = Settings('test',1,Path('unused'),'INFO','http://localhost','test',90)
        self.assertEqual(s.batch_pause_seconds, 0.6)
        self.assertEqual(s.batch_max_seconds, 3)

    def test_warmup_does_not_accept_a_cached_result_as_model_readiness(self):
        t = LocalTranslator('http://localhost:11434')
        t._translate_selected = Mock(side_effect=AssertionError('No result cache for warmup'))
        t._translate_uncached = Mock(return_value=TranslationResult('en', 'ru', 'Привет'))
        t.warmup()
        t._translate_uncached.assert_called_once_with('Hello!', 'en', 'ru')

    def test_configuration_rejects_unsafe_or_nonfinite_batch_settings(self):
        for key, value in [('OLLAMA_NUM_BATCH', '999'), ('BATCH_PAUSE_SECONDS', 'nan'),
                           ('BATCH_MAX_SECONDS', 'inf'), ('BATCH_PAUSE_SECONDS', '6')]:
            with self.subTest(key=key, value=value), patch.dict('os.environ',
                {'DISCORD_TOKEN':'test','DISCORD_GUILD_ID':'1','DISCORD_ADDITIONAL_GUILD_IDS':'',
                 'DISCORD_DEFAULT_OFF_GUILD_IDS':'',key:value}):
                with self.assertRaises(RuntimeError):
                    Settings.load()
