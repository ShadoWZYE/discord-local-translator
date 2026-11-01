import json
import tempfile
import sqlite3
import time
from contextlib import closing
import unittest
from pathlib import Path
from unittest.mock import Mock

from translator_bot.translation import LocalTranslator, TranslationResult
from translator_bot.translation_policy import standalone_parts,dependency_reasons,dependency_span,effective_context
from translator_bot.edit_translation import edit_window
from translator_bot.result_cache import ResultCache


class LocalFastPathTests(unittest.TestCase):
    def test_cold_local_render_needs_neither_database_nor_model(self):
        t=LocalTranslator('http://localhost')
        t._disk_cache=Mock()
        t._disk_cache.get.side_effect=AssertionError('Local rendering must not read SQLite')
        t._request=Mock(side_effect=AssertionError('Local rendering must not call model'))
        for source,target,text,expected in (
                ('en','ru','Mrrr... 😺','Мррр... 😺'),
                ('ru','en','мрррр ~','mrrrr ~'),
                ('en','ru','Meow!','Мяу!')):
            result=t.cached_translation(text,source,target)
            self.assertIsNotNone(result)
            self.assertEqual(result.text,expected)
        self.assertEqual(t._cache_stats['lookup_local_hits'],3)
        self.assertEqual(t._cache_stats['lookup_fragment_hits'],0)
        t._request.assert_not_called()

    def test_purr_never_matches_words_handles_commands_or_sentences(self):
        for text in ('Mr.','Mrs.','Murray','Mrrr is my name.','@mrrr','/mrrr',
                     '`mrrr`','mrrr https://example.org','mrrr\nHello','mrrr <@123>'):
            self.assertIsNone(LocalTranslator._cat_sound(text,'en','ru'),text)
        self.assertIsNone(LocalTranslator._cat_sound('mrrr','en','de'))


class CacheRenderingTests(unittest.TestCase):
    def test_decorations_share_lexical_cache_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cache.sqlite3'
            first=LocalTranslator('http://localhost',cache_path=path)
            first._translate_uncached=Mock(return_value=TranslationResult('en','ru','Готово. ~'))
            first.translate_to('Done. ~','ru','en')
            second=LocalTranslator('http://localhost',cache_path=path)
            second._request=Mock(side_effect=AssertionError('No model for formatting changes'))
            for original,expected in [('Done! ❤️','Готово! ❤️'),('  Done????','  Готово????'),('Done','Готово')]:
                with second.conversation_context(json.dumps({'originals':[{'text':'Unrelated history'}]})):
                    self.assertEqual(second.cached_translation(original,'en','ru').text,expected)
            self.assertEqual(second.cache_stats()['lookup_fragment_hits'],3)

    def test_only_closed_vocabulary_not_arbitrary_clause_normalization(self):
        for text in ('I am done.','Done with him.','helloAlice','`Hello`','Hello <@123>','hello\nworld','Hello: https://example.org'):
            self.assertIsNone(standalone_parts(text,'en','ru'),text)
        self.assertIsNone(standalone_parts('Done!','en','ar'))
        self.assertTrue(dependency_reasons('She said so.','en','ru'))
        self.assertIn('unfinished',dependency_reasons('because I','en','ru'))
        self.assertIn('register',dependency_reasons('How are you?','en','ru'))
        self.assertEqual(dependency_reasons('I will bring coffee.','en','ru'),('lexical_context',))
        self.assertEqual(dependency_reasons('Thank you!','en','ru'),())

    def test_normalized_flags_invalidate_variants_and_inflight_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cache.sqlite3'
            t=LocalTranslator('http://localhost',cache_path=path)
            t.remember_reviewed('Done!','en','ru','Готово!')
            self.assertEqual(t.translate_to('Done????','ru','en').text,'Готово????')
            _,pending=t._get_fragment('Done','en','ru',count=False)
            other=LocalTranslator('http://localhost',cache_path=path)
            other.invalidate_source('Done!')
            t._put_fragment(pending,'Старое')
            self.assertIsNone(t.cached_translation('Done????','en','ru'))
            self.assertIsNone(other.cached_translation('Done!','en','ru'))

    def test_cache_assembly_keeps_blank_lines_tabs_emoji_and_punctuation(self):
        t=LocalTranslator('http://localhost')
        t.remember_reviewed('Hello!','en','ru','Привет!')
        t.remember_reviewed('Thank you!','en','ru','Спасибо!')
        original='  Hello?! ❤️\n\n\tThank you... ~'
        self.assertEqual(t.cached_translation(original,'en','ru').text,'  Привет?! ❤️\n\n\tСпасибо... ~')

    def test_done_drops_irrelevant_context_but_ellipsis_and_regeneration_keep_it(self):
        context=json.dumps({'originals':[{'text':'A long earlier conversation.'}]})
        self.assertEqual(effective_context('Done. ~',context,'en','ru'),'')
        self.assertNotEqual(effective_context('It is done.',context,'en','ru'),'')
        self.assertIn('regenerate',effective_context('Done!',json.dumps({'request':'regenerate'}),'en','ru'))

    def test_leading_mentions_are_rendered_not_generated_and_body_mentions_stay(self):
        t=LocalTranslator('http://localhost')
        t._request=Mock(return_value='Пожалуйста, пришли напоминание.')
        self.assertEqual(t.translate_to('<@123>  Please send a reminder.','ru','en').text,'<@123>  Пожалуйста, пришли напоминание.')
        self.assertNotIn('DCTOKEN',t._request.call_args.args[0])
        t._request.assert_called_once()
        protected,values=t._protect('Send <@123> a reminder.','en','ru')
        self.assertIn('<@123>',values.values())

    def test_dependency_span_expands_broken_sentence_and_pronoun_context(self):
        lines=['A separate note.','','My brother arrived.','He said','that he was tired.','A separate task.']
        self.assertEqual(dependency_span(lines,3,4,'en','ru'),(2,5))
        self.assertEqual(dependency_span(['I will bring coffee.','I will bring cake.'],1,2,'en','ru'),(1,2))

    def test_independent_edits_refresh_one_line_not_three(self):
        before='\n'.join(f'I will bring coffee to morning meeting number {i}.' for i in range(15))
        after=before.replace('coffee','sandwiches',1)
        translated='\n'.join('Я принесу кофе на утреннюю встречу.' for _ in range(15))
        self.assertEqual(edit_window(before,after,translated,source='en',target='ru'),(0,1,0,1))

    def test_wal_writer_cannot_turn_valid_cache_read_into_false_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cache.sqlite3'
            cache=ResultCache(path)
            _,revision=cache.get(b'key',b'source')
            cache.put(b'key','value',revision,b'source')
            with closing(sqlite3.connect(path)) as writer:
                writer.execute('BEGIN IMMEDIATE')
                begin=time.monotonic()
                self.assertEqual(cache.get(b'key',b'source'),('value',revision))
                self.assertLess(time.monotonic()-begin,.15)

    def test_markup_and_multiline_are_reassembled_without_model_formatting(self):
        t=LocalTranslator('http://localhost')
        def respond(prompt,**kwargs):
            parts=json.loads(prompt.rsplit('INPUT: ',1)[1])
            self.assertFalse(any('\n' in value or '**' in value or value=='_' for value in parts.values()))
            translations=iter(('Первая строка','Вторая строка'))
            return json.dumps({key:next(translations) for key in kwargs['format_schema']['required']})
        t._request=Mock(side_effect=respond)
        self.assertEqual(t.translate_to('**First line**\n\n_Second line_','ru','en').text,'**Первая строка**\n\n_Вторая строка_')
        t._request.assert_called_once()

    def test_slot_may_not_add_newlines_or_absorb_another_source_line(self):
        from translator_bot.translation import TranslationUnavailable
        t=LocalTranslator('http://localhost')
        def respond(prompt,**kwargs):
            return json.dumps({key:'Первая строка\nВторая строка' for key in kwargs['format_schema']['required']})
        t._request=Mock(side_effect=respond)
        with self.assertRaises(TranslationUnavailable):
            t.translate_to('**First line**\n_Second line_','ru','en')

    def test_cached_typed_smileys_still_allow_intentional_rendering(self):
        t=LocalTranslator('http://localhost')
        for source,value in [('Спасибо.)','Thanks. 🙂'),('Да))','Yes 😄'),('Да)))','Yes 😆')]:
            self.assertTrue(t._cached_valid(source,value,'ru','en'),(source,value))
        self.assertFalse(t._cached_valid('Спасибо. ❤️','Thanks.','ru','en'))

    def test_source_triggered_family_and_vocative_guidance_has_literal_controls(self):
        t=LocalTranslator('http://localhost')
        self.assertIn('not imply father speaking to HIS brother',t._prompt('Отец связывался с братом.','ru','en'))
        self.assertIn('affectionate sunshine',t._prompt('Солнце просто аыскажись мне','ru','en'))
        self.assertNotIn('affectionate sunshine',t._prompt('Солнце светит ярко.','ru','en'))
        self.assertNotIn('not imply father speaking to HIS brother',t._prompt('Он разговаривал с другом.','ru','en'))
        self.assertIn('beneficial purpose',t._prompt('Ты делаешь все на благо!','ru','en'))
        self.assertNotIn('beneficial purpose',t._prompt('Ты делаешь все хорошо!','ru','en'))

    def test_fixed_slots_cannot_repeat_the_subject_on_a_broken_sentence(self):
        from translator_bot.translation import TranslationUnavailable
        source='I **really**\nneed your help.'
        with self.assertRaises(TranslationUnavailable):
            LocalTranslator._validate_slot_subjects(source,'Я **действительно**\nмне нужна твоя помощь.','en','ru')
        self.assertFalse(LocalTranslator('http://localhost')._cached_valid(source,'Я **действительно**\nмне нужна твоя помощь.','en','ru'))
        LocalTranslator._validate_slot_subjects(source,'Я **действительно**\nнуждаюсь в твоей помощи.','en','ru')
        LocalTranslator._validate_slot_subjects(source,'Мне **действительно**\nнужна твоя помощь.','en','ru')

    def test_family_ownership_is_grounded_in_same_speaker_not_nearest_noun(self):
        t=LocalTranslator('http://localhost')
        from translator_bot.translation import TranslationUnavailable
        source='По итогу отец никак не связывался с братом. Отругала брата.'
        context={'current_speaker':{'id':'A'},'originals':[{'speaker':{'id':'A'},
            'speaker_relation':'current_speaker','text':'Я разговаривала по телефону с отцом и меня отругали за брата.'}]}
        with t.conversation_context(json.dumps(context)):
            self.assertTrue(t._speaker_family_context(source))
            with self.assertRaises(TranslationUnavailable):
                t._validate_reference_roles(source,"Dad didn't call his brother.")
            t._validate_reference_roles(source,"Dad didn't call my brother.")
            self.assertFalse(t._speaker_family_context('Отец связывался со своим братом.'))
        context['originals'][0]['speaker_relation']='other_speaker'
        context['originals'][0]['speaker']['id']='B'
        with t.conversation_context(json.dumps(context)):
            self.assertFalse(t._speaker_family_context(source))

    def test_sunshine_and_literal_wording_contract_not_generic_affection(self):
        t=LocalTranslator('http://localhost')
        source='Солнце просто аыскажись мне'
        self.assertFalse(t._cached_valid(source,'Sweetheart, just tell me what is on your mind.','ru','en'))
        self.assertTrue(t._cached_valid(source,'Sunshine, just speak your mind to me.','ru','en'))
        self.assertTrue(t._cached_valid('Солнце светит ярко.','The sun is shining brightly.','ru','en'))
        with t.conversation_context(json.dumps({'originals':[{'text':'An earlier conversation'}]})):
            prompt=t._prompt(source,'ru','en')
        self.assertIn('closest direct lexical equivalents',prompt)
        self.assertIn('Context is NOT permission to replace source words',prompt)
        self.assertFalse(t._cached_valid('Ты делаешь все на благо!','Everything you do is meant to help!','ru','en'))
        self.assertTrue(t._cached_valid('Ты делаешь все на благо!','You do everything for a good purpose!','ru','en'))
        self.assertTrue(t._cached_valid('Это во благо жителей.','This is for the benefit of residents.','ru','en'))


if __name__=='__main__':unittest.main()
