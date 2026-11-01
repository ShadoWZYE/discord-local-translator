import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from translator_bot.translation import LocalTranslator
from translator_bot.translation_policy import dependency_reasons,independent_unit,dependency_span,actor_drift
from scripts.cache_report import report


class RequestObservationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'cache.sqlite3'
        self.t=LocalTranslator('http://localhost',cache_path=self.path)

    def observe(self,message,text,origin='automatic'):
        with self.t.request_observation(message,text,origin):
            self.t._observe_fragment('A small bird.','en','ru',parent=text,hit=False)

    def test_attempts_edits_and_origins_do_not_create_distinct_message_repeats(self):
        self.observe(1,'first');self.observe(1,'first');self.observe(1,'edited')
        self.observe(1,'edited','retry');self.observe(1,'edited','regeneration')
        result=report(self.path)
        self.assertEqual(result['live_repeated_units'],[])
        automatic=next(r for r in result['request_observations_by_origin'] if r['origin']=='automatic')
        self.assertEqual((automatic['unit_versions'],automatic['attempts'],automatic['distinct_messages']),(2,3,1))
        self.observe(2,'different')
        self.assertEqual(report(self.path)['live_repeated_units'][0]['distinct_messages'],2)

    def test_request_identity_does_not_change_cache_identity_and_scope_restores(self):
        before=self.t._cache_fingerprint('Thanks','en','ru')
        with self.t.request_observation(1,'Thanks','retry'):
            self.assertEqual(before,self.t._cache_fingerprint('Thanks','en','ru'))
            with self.t.request_observation(2,'Thanks','regeneration'):pass
            self.assertEqual(self.t._context.observation[2],'retry')
        self.assertIsNone(self.t._context.observation)

    def test_fixed_punctuation_emoji_and_laughter_not_observed(self):
        self.t._request=Mock(return_value=json.dumps({'part_0':'Птица летит.'}))
        with self.t.request_observation(1,'A bird flies.\n~\n😂','automatic'):
            self.t.translate_to('A bird flies.\n~\n😂','ru','en')
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM request_fragment_observations').fetchone()[0],1)
        self.t._observe_fragment('~ 😂','en','ru',parent='~ 😂',hit=False)
        self.assertEqual(self.t._disk_cache.observation_stats()['distinct_units'],1)

    def test_russian_inflected_referents_require_context_not_word_substrings(self):
        for word in ('им','ими','ему','ней','нему','ним','ними','них','него','этим','того','этой'):
            with self.subTest(word=word):
                self.assertIn('referent',dependency_reasons('Я помогаю '+word+'.','ru','en'))
                self.assertFalse(independent_unit('Я помогаю '+word+'.','ru','en'))
        for text in ('Я куплю лимон.','Я принесу книгу.','Я чиню двигатель.'):
            self.assertNotIn('referent',dependency_reasons(text,'ru','en'))
        self.assertEqual(dependency_span(['Я купил велосипед.','Я доволен им.'],1,2,'ru','en'),(0,2))

    def test_only_balanced_generated_emphasis_is_unwrapped_without_inference(self):
        clean=LocalTranslator._remove_generated_emphasis
        for value in ('I chose *that* one.','I chose **that** one.','I chose _that_ one.'):
            self.assertEqual(clean('Я выбрал тот.',value),'I chose that one.')
        for value in ('3 * 4','*unfinished','word*word','**2**','*two\nlines*'):
            self.assertEqual(clean('Исходный текст.',value),value)
        self.assertEqual(clean('Keep *this*.','Keep *this*.'),'Keep *this*.')

    def test_generated_emphasis_does_not_trigger_segmented_retry(self):
        self.t._request=Mock(return_value=json.dumps({'segment_0':'I chose *that* one.'}))
        result=self.t._translate_segmented_layout('Я выбрал тот.😂','ru','en')
        self.assertEqual(result,'I chose that one.😂')
        self.t._request.assert_called_once()

    def test_invented_brackets_get_bounded_reason_aware_retry_not_silent_removal(self):
        self.t._request=Mock(side_effect=['Я куплю [чай].','Я куплю чай.'])
        result=self.t.translate_to('I will buy tea.','ru','en')
        self.assertEqual(result.text,'Я куплю чай.')
        self.assertEqual(self.t._request.call_count,2)
        self.assertIn('Preserve source delimiters',self.t._request.call_args.args[0])

    def test_repeated_editorial_brackets_still_fail_and_never_cache(self):
        self.t._request=Mock(return_value='Я куплю [чай].')
        from translator_bot.translation import TranslationUnavailable
        with self.assertRaises(TranslationUnavailable):self.t.translate_to('I will buy tea.','ru','en')
        self.assertEqual(self.t._request.call_count,2)
        self.assertIsNone(self.t.cached_translation('I will buy tea.','en','ru'))

    def test_unseen_english_subject_verbs_do_not_trigger_false_actor_reversal(self):
        self.assertFalse(actor_drift('Ты замечаешь детали, надо учиться.',
            'You notice details; I need to study.','ru','en'))
        self.assertTrue(actor_drift('Я замечаю детали.','You notice details.','ru','en'))
        self.assertTrue(actor_drift('Ты замечаешь детали.','I notice details.','ru','en'))
        self.assertTrue(actor_drift('Ты замечаешь детали.','I follow you everywhere.','ru','en'))


if __name__=='__main__':unittest.main()
