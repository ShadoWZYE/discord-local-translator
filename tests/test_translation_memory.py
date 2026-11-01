from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock

from translator_bot.translation import LocalTranslator, TranslationResult
from translator_bot.translation_memory import validate_memory_review
from translator_bot.review_evidence import CHECKS,text_hash
from translator_bot.translation_policy import lexical_envelope,standalone_parts,memory_dependency_block,has_portable_sentence


class TranslationMemoryTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)/'cache.sqlite3'
        self.t=LocalTranslator('http://localhost',cache_path=self.path)

    def evidence(self,source='Добрый день',target='Good afternoon',**changes):
        scope=self.t._source_key('fragment:'+source)
        _,revision=self.t._disk_cache.get(b'not-a-key',scope)
        record={'decision':'translation_memory','source_text':source,'candidate_text':target,
            'source_sha256':text_hash(source),'candidate_sha256':text_hash(target),
            'source_language':'ru','target_language':'en','source_message_ids':[],'context':[],
            'clauses':[{'source_span':source,'candidate_span':target,
                'finding':'The greeting has an explicit and conventional lexical equivalent.',
                'change_reason':'Approve only the exact construction, not similar unseen wording.'}],
            'checks':{key:'Source-first review retains all explicit lexical and grammatical meaning.' for key in CHECKS},
            'uncertainty':'No material ambiguity for the specifically approved usage.',
            'reference_check':{'kind':'source_first_lexical_review','independent':False,
                'finding':'Direct lexical reconstruction; structural validation alone is not certification.'},
            'scope_kind':'portable','decoration':'literal_envelope','context_independent':True,
            'scope_reason':'An explicit greeting has no unresolved referent, inferred gender, register selection or alternate lexical object.',
            'counterexamples':['Longer phrases must be reviewed separately.','The reverse language direction is not approved.'],
            'invalidation_revision':list(revision)}
        record.update(changes)
        return record

    def approve(self,record):
        return self.t.approve_translation_memory(record['source_text'],record['source_language'],
            record['target_language'],record['candidate_text'],record)

    def test_new_phrase_not_whitelisted_can_be_approved_and_survives_restart(self):
        record=self.evidence();self.assertIsNone(standalone_parts(record['source_text'],'ru','en'))
        self.approve(record)
        restarted=LocalTranslator('http://localhost',cache_path=self.path)
        restarted._request=Mock(side_effect=AssertionError('No model for reviewed memory'))
        with restarted.conversation_context(json.dumps({'originals':[{'text':'Different conversation'}]})):
            result=restarted.cached_translation('**Добрый день!!** ❤️','ru','en')
            self.assertEqual(result.text,'**Good afternoon!!** ❤️')
            self.assertEqual(result.reused_characters,result.total_characters)
        restarted._request.assert_not_called()
        self.assertIsNone(restarted.cached_translation('Добрый день всем!','ru','en'))
        self.assertIsNone(restarted.cached_translation('Good afternoon','en','ru'))

    def test_portability_is_separate_from_corrective_or_no_change_approval(self):
        for change in ({'decision':'confirmed_error'},{'decision':'acceptable_variant'},
                       {'context_independent':False},{'scope_reason':'Fluent'}, {'counterexamples':[]},
                       {'candidate_sha256':'stale'},{'invalidation_revision':[999,999]}):
            record=self.evidence(**change)
            with self.assertRaises(ValueError):self.approve(record)
        self.assertEqual(self.t._disk_cache.memory_count(),0)

    def test_english_you_and_gender_are_not_approved_globally(self):
        for source,target in [('You are ready','Ты готов'),('I was ready','Я был готов')]:
            record=self.evidence(source,target,source_language='en',target_language='ru')
            with self.assertRaises(ValueError):self.approve(record)

    def test_approved_memory_outlives_result_ttl_and_lru_and_generation(self):
        self.approve(self.evidence())
        self.t._disk_cache.limit=1
        for i in range(3):self.t._disk_cache.put(bytes([i]),'generated',0)
        with closing(sqlite3.connect(self.path)) as db,db:db.execute('UPDATE results SET expires=0')
        self.assertEqual(self.t.cached_translation('Добрый день!','ru','en').text,'Good afternoon!')
        self.assertEqual(self.t._disk_cache.memory_count(),1)

    def test_flag_revokes_durable_variants_and_prevents_stale_approval(self):
        evidence=self.evidence();self.approve(evidence)
        other=LocalTranslator('http://localhost',cache_path=self.path)
        other.invalidate_source('||Добрый день?!|| ❤️')
        self.assertIsNone(self.t.cached_translation('Добрый день!','ru','en'))
        with self.assertRaises(ValueError):self.approve(evidence)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memory_approvals').fetchone()[0],1)

    def test_explicit_regeneration_cannot_replay_durable_memory(self):
        self.approve(self.evidence())
        with self.t.conversation_context(json.dumps({'request':'regenerate'})):
            self.assertIsNone(self.t.cached_translation('Добрый день!','ru','en'))

    def test_exact_only_approval_does_not_rewrite_punctuation_or_markup(self):
        record=self.evidence('Добрый день.','Good afternoon.',decoration='exact')
        self.approve(record)
        self.assertEqual(self.t.cached_translation('Добрый день.','ru','en').text,'Good afternoon.')
        self.assertIsNone(self.t.cached_translation('Добрый день!','ru','en'))

    def test_block_approval_keeps_paragraph_and_exact_external_context(self):
        source='I will bring bottled water.';target='Я принесу бутилированную воду.'
        parent='Packing supplies.\n'+source+'\n\nThe weather forecast is cloudy.'
        context=json.dumps({'originals':[{'text':'Planning a hiking trip.'}]})
        record=self.evidence(source,target,source_language='en',target_language='ru',
            scope_kind='dependency_block',decoration='exact',context_independent=False,
            parent_text=parent,conversation_context=context,source_message_ids=[1],
            context=[{'message_id':2,'sha256':text_hash('Planning a hiking trip.')}],
            reference_check={'kind':'source_first_human_context','independent':False,
                'finding':'The exact human reference is preserved; omitted paragraphs are explicitly reviewed.'})
        self.approve(record)
        changed=parent.replace('The weather forecast is cloudy.','The calendar shows Tuesday.')
        with self.t.conversation_context(context):
            self.assertEqual(self.t._cached_memory(source,'en','ru',parent=changed),target)
            self.assertIsNone(self.t._cached_memory(source,'en','ru',parent=changed.replace('Packing supplies.','Discussing a metaphor.')))
        with self.t.conversation_context(json.dumps({'originals':[{'text':'A different discussion.'}]})):
            self.assertIsNone(self.t._cached_memory(source,'en','ru',parent=changed))
        self.assertIsNone(self.t._cached_memory(source,'en','ru',parent=source+'\n'+source))

    def test_unreviewed_prose_never_loses_parent_scope(self):
        source='I will bring a bat.'
        _,token=self.t._get_fragment(source,'en','ru',parent='Baseball practice.\n'+source)
        self.t._put_fragment(token,'Я принесу биту.')
        self.assertIsNone(self.t._get_fragment(source,'en','ru',parent='Rescuing animals.\n'+source)[0])

    def test_approved_field_reuses_joint_layout_and_reports_partial_source_chars(self):
        self.approve(self.evidence())
        self.t._request=Mock(return_value=json.dumps({'part_1':'The train is arriving.'}))
        original='Добрый день!\nПоезд прибывает.'
        result=self.t.translate_to(original,'en','ru')
        self.assertEqual(result.text,'Good afternoon!\nThe train is arriving.')
        self.assertEqual(self.t._request.call_args.kwargs['format_schema']['required'],['part_1'])
        self.assertEqual(result.reused_characters,len('Добрый день!'))
        self.assertEqual(result.total_characters,len(original))
        self.assertEqual(self.t.cache_stats()['partial_cache_requests'],1)

    def test_short_sentences_reuse_known_phrase_without_dropping_joint_context(self):
        self.approve(self.evidence('Спасибо','Thanks'))
        self.t._request=Mock(return_value=json.dumps({'part_1':'The train is arriving.'}))
        original='Спасибо! Поезд прибывает.'
        result=self.t.translate_to(original,'en','ru')
        self.assertEqual(result.text,'Thanks! The train is arriving.')
        self.assertEqual(self.t._request.call_args.kwargs['format_schema']['required'],['part_1'])
        self.assertIn('Спасибо!',self.t._request.call_args.args[0])
        self.assertIn('Поезд прибывает.',self.t._request.call_args.args[0])
        self.assertEqual(result.reused_characters,len('Спасибо!'))

    def test_short_fully_cached_sentences_skip_inference_and_respect_invalidation(self):
        self.approve(self.evidence('Спасибо','Thanks'))
        self.approve(self.evidence('Да','Yes'))
        self.t._request=Mock(side_effect=AssertionError('No inference'))
        self.assertEqual(self.t.cached_translation('Спасибо! Да.','ru','en').text,'Thanks! Yes.')
        self.t.invalidate_source('Спасибо')
        self.assertIsNone(self.t.cached_translation('Спасибо! Да.','ru','en'))
        self.t._request.assert_not_called()

    def test_short_sentence_boundaries_do_not_promote_unknown_prose_or_broken_phrases(self):
        for text in ('Нет бананов. Я куплю яблоки.','No bananas. This is ready.',
                     'Dr. Smith arrived. He waved.','I will bring a bat. It is ready.'):
            self.assertFalse(has_portable_sentence(text,'en','ru'))
        self.assertFalse(has_portable_sentence('No\nbananas','en','ru'))

    def test_repetition_diagnostics_are_bounded_hashes_not_translation_approvals(self):
        for parent in ('First context.','Second context.'):
            self.t._observe_fragment('I will bring a bat.','en','ru',parent=parent,hit=False)
        stats=self.t._disk_cache.observation_stats()
        self.assertEqual(stats['repeated_misses'],1)
        self.assertEqual(stats['eligible_repeated_misses'],1)
        self.assertEqual(stats['changed_scope_misses'],1)
        self.assertEqual(self.t._disk_cache.memory_count(),0)
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT * FROM fragment_observations').fetchone()
            self.assertNotIn('bat',str(row))

    def test_memory_reads_do_not_depend_on_ordinary_cache_identity(self):
        self.approve(self.evidence())
        source='Добрый день!'
        scope=self.t._whole_scope(source,'ru','en')
        fingerprint=self.t._cache_fingerprint(source,'ru','en')
        _,revision=self.t._disk_cache.get(fingerprint,scope)
        self.t._disk_cache.put(fingerprint,'Неправильно',revision,scope)
        self.assertEqual(self.t.cached_translation(source,'ru','en').text,'Good afternoon!')

    def test_whole_span_formatting_only_never_code_links_or_partial_markup(self):
        self.t.remember_reviewed('Thank you.','en','ru','Спасибо.')
        for text,expected in [('**Thank you!** ❤️','**Спасибо!** ❤️'),
                              ('||Thank you??||','||Спасибо??||'),
                              ('__*Thank you*__ ~','__*Спасибо*__ ~')]:
            with self.subTest(text=text):self.assertEqual(self.t.cached_translation(text,'en','ru').text,expected)
        for text in ('`Thank you`','[Thank you](https://example.org)','**Thank** you','**Thank you*','Thank_you','Thank you\nthere'):
            self.assertIsNone(lexical_envelope(text),text)


if __name__=='__main__':unittest.main()
