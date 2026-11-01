import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from translator_bot.translation import LocalTranslator, TranslationResult, TranslationUnavailable


class TranslationCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = LocalTranslator('http://localhost:11434', glossary_path=Path(self.tmp.name)/'glossary.json')
        self.t._translate_uncached = Mock(return_value=TranslationResult('en', 'ru', 'Привет'))

    def generate(self, text='Hello'):
        return self.t.translate_to(text, 'ru', 'en')

    def test_identical_success_reuses_validated_result(self):
        self.assertEqual(self.generate(), self.generate())
        self.t._translate_uncached.assert_called_once()

    def test_context_source_target_text_and_model_are_part_of_identity(self):
        self.generate()
        with self.t.conversation_context('different speaker/reply'):
            self.generate()
        self.generate('Hello again')
        self.t.translate_to('Hello', 'es', 'en')
        self.t.translate_to('Hello', 'ru', 'es')
        self.t.model = 'different-model'
        self.generate()
        self.assertEqual(self.t._translate_uncached.call_count, 6)

    def test_glossary_content_change_invalidates_identity(self):
        self.generate()
        self.t.glossary_path.write_text('{"en":{"hello":"greeting"}}', encoding='utf-8')
        self.generate()
        self.t.glossary_path.write_text('{"en":{"hello":"changed"}}', encoding='utf-8')
        self.generate()
        self.assertEqual(self.t._translate_uncached.call_count, 3)

    def test_failures_and_empty_results_are_not_cached(self):
        self.t._translate_uncached.side_effect = [TranslationUnavailable('offline'), None,
            TranslationResult('en', 'ru', 'Привет')]
        with self.assertRaises(TranslationUnavailable):
            self.generate()
        self.assertIsNone(self.generate())
        self.assertIsNotNone(self.generate())
        self.assertEqual(self.t._translate_uncached.call_count, 3)

    def test_ttl_and_lru_are_bounded(self):
        self.t.cache_ttl_seconds = 0
        self.generate()
        self.generate()
        self.assertEqual(self.t._translate_uncached.call_count, 2)
        self.t.cache_ttl_seconds = 300
        self.t.cache_limit = 2
        for text in ('One', 'Two', 'Three'):
            self.generate(text)
        self.assertEqual(len(self.t._result_cache), 2)
        self.generate('One')
        self.assertEqual(self.t._translate_uncached.call_count, 6)

    def test_clear_during_generation_does_not_repopulate_old_cache(self):
        def generate(*args):
            self.t.clear_cache()
            return TranslationResult('en', 'ru', 'Привет')
        self.t._translate_uncached.side_effect = generate
        self.generate()
        self.generate()
        self.assertEqual(len(self.t._result_cache), 0)
        self.assertEqual(self.t._translate_uncached.call_count, 2)

    def test_concurrent_identical_requests_share_one_generation(self):
        entered, release, sharing = threading.Event(), threading.Event(), threading.Event()
        def generate(*args):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Test gate timed out')
            return TranslationResult('en', 'ru', 'Привет')
        self.t._translate_uncached.side_effect = generate
        logger = Mock()
        logger.info.side_effect = lambda msg, *args: sharing.set() if msg.startswith('Sharing') else None
        with patch('translator_bot.translation.logging.getLogger', return_value=logger), ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.generate)
            self.assertTrue(entered.wait(2))
            second = pool.submit(self.generate)
            try:
                self.assertTrue(sharing.wait(2))
            finally:
                release.set()
            self.assertEqual(first.result(2), second.result(2))
        self.t._translate_uncached.assert_called_once()
        self.assertEqual(self.t._inflight, {})
