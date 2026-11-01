"""Synthetic cache contracts, never production linguistic evidence."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from translator_bot.translation_policy import independent_unit,dependency_reasons,effective_context
from translator_bot.reuse_discovery import discover
from translator_bot.reuse_planner import candidate_units
from translator_bot.reuse_store import ReuseStore
from translator_bot.result_cache import ResultCache
from translator_bot.translation import LocalTranslator,TranslationResult


class CompletenessTests(unittest.TestCase):
    def test_missing_period_is_not_incompleteness_or_portability(self):
        for text in ('I will check tomorrow','The engine is running','We can start tomorrow'):
            self.assertTrue(independent_unit(text,'en','ru'),text)
            self.assertEqual(dependency_reasons(text,'en','ru'),('lexical_context',))
            self.assertNotEqual(effective_context(text,'human A','en','ru'),effective_context(text,'human B','en','ru'))
        self.assertTrue(independent_unit('Я проверю завтра','ru','en'))
        self.assertNotIn('unfinished',dependency_reasons('Clear sky','en','ru'))
        self.assertFalse(independent_unit('Clear sky','en','ru'))

    def test_incomplete_dependent_and_register_stay_blocked(self):
        for text in ('I will check with','I will not','The engine is','We can start because',
                     'We can start...','We can start,','I will check (tomorrow'):
            self.assertFalse(independent_unit(text,'en','ru'),text)
            self.assertIn('unfinished',dependency_reasons(text,'en','ru'))
        self.assertFalse(independent_unit('I will check it tomorrow','en','ru'))
        self.assertFalse(independent_unit('You can start tomorrow','en','ru'))
        self.assertIn('register',dependency_reasons('You can start tomorrow','en','ru'))
        self.assertFalse(independent_unit('Я хочу','ru','en'))

    def test_new_context_bound_fragment_reuses_only_same_parent_and_context(self):
        with tempfile.TemporaryDirectory() as folder:
            t=LocalTranslator('http://localhost',cache_path=Path(folder)/'cache.db')
            with t.conversation_context('human A'):
                _,token=t._get_fragment('The engine is running','en','ru',parent='The engine is running')
                self.assertTrue(t._put_fragment(token,'Двигатель работает'))
                self.assertEqual(t._get_fragment('The engine is running','en','ru',parent='The engine is running')[0],'Двигатель работает')
                self.assertIsNone(t._get_fragment('The engine is running','en','ru',parent='New topic. The engine is running')[0])
            with t.conversation_context('human B'):
                self.assertIsNone(t._get_fragment('The engine is running','en','ru',parent='The engine is running')[0])


class DiscoveryTests(unittest.TestCase):
    def rows(self):
        return [dict(source_id=i,source_language='ru',target_language='en',
            source_text=f'Сервер S{i} доступен.',translated_text=f'Server S{i} is available.') for i in range(1,4)]

    def test_ngrams_stay_inside_fields_and_do_not_starve(self):
        units=candidate_units('Alpha beta. Gamma delta\nEpsilon zeta')
        self.assertNotIn(('ngram','beta Gamma'),units)
        self.assertNotIn(('ngram','delta Epsilon'),units)
        self.assertIn(('ngram','Alpha beta'),units)
        units=candidate_units(' '.join('word'+chr(97+i%26) for i in range(200)))
        self.assertTrue(any(k=='ngram' for k,_ in units));self.assertLessEqual(len(units),96)
        self.assertFalse(any('secret' in u for _,u in candidate_units('`secret code` https://example.com/secret')))
        self.assertNotIn(('ngram','left right'),candidate_units('left `code` right'))
        self.assertNotIn(('ngram','red light'),candidate_units('red, light'))

    def test_template_nomination_requires_distinct_sources_and_slot_values(self):
        r=discover(self.rows()*2)
        c=r['constructions'][0]
        self.assertEqual(c['distinct_sources'],3);self.assertEqual(c['distinct_slot_values'],3)
        self.assertTrue(c['eligible_for_review']);self.assertEqual(c['decision'],'unreviewed_candidate')
        self.assertFalse(discover(self.rows(),flagged=[1])['constructions'][0]['eligible_for_review'])
        self.assertTrue(all(not p['eligible_for_review'] for p in r['phrases']))

    def test_conflicting_target_or_moved_or_missing_code_never_approves(self):
        rows=self.rows();rows[0]['translated_text']='Server S1 is unavailable.'
        self.assertTrue(all(not c['eligible_for_review'] for c in discover(rows)['constructions']))
        rows=self.rows()
        for row in rows:row['translated_text']='Different prose without code.'
        self.assertEqual(discover(rows)['constructions'],[])


class RecentAuditTests(unittest.TestCase):
    def test_recent_summary_excludes_manual_legacy_retries_and_combines_phases(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ReuseStore(ResultCache(Path(folder)/'cache.db'))
            def add(message,origin='automatic',phase='translation',**changes):
                payload=dict(diagnostics_version=2,status='completed',model_calls=1,returned_translation=True,
                    whole_misses={'cold':1},fragment_blockers={'unproven':1},candidates=[],directions=[['ru','en']])
                payload.update(changes);store.audit(message,b'version',origin,phase,payload)
            add(b'one',phase='probe',model_calls=0,returned_translation=False)
            add(b'one',reused_characters=4)
            add(b'one',reused_characters=4)
            add(b'two',phase='probe',model_calls=0,whole_misses={})
            add(b'ignored',origin='manual');add(b'retry',origin='retry')
            add(b'legacy',diagnostics_version=1)
            r=store.recent_summary()
            self.assertEqual(r['samples'],2);self.assertEqual(r['partial_inference'],1)
            self.assertEqual(r['full_cache'],1);self.assertEqual(r['whole_misses'],{'cold':1})
            self.assertEqual(store.recent_summary(limit=1)['samples'],1)
            add(b'one',phase='probe',model_calls=0,whole_misses={})
            self.assertEqual(store.recent_summary()['full_cache'],2)

    def test_real_audit_binds_reason_without_model_or_plaintext(self):
        with tempfile.TemporaryDirectory() as folder:
            t=LocalTranslator('http://localhost',cache_path=Path(folder)/'cache.db')
            t._translate_uncached=Mock(return_value=TranslationResult('ru','en','Tomorrow'))
            with t.request_observation(1,'Завтра','automatic',phase='translation'):
                t._translate_selected('Завтра','ru','en')
            r=t.cache_stats()['recent_live_cache']
            self.assertEqual(r['whole_misses'],{'cold':1});self.assertEqual(r['samples'],1)
            with closing(sqlite3.connect(t._disk_cache.path)) as db:
                raw=db.execute('SELECT payload FROM reuse_requests').fetchone()[0]
            self.assertNotIn('Завтра',raw);self.assertNotIn('Tomorrow',raw)
