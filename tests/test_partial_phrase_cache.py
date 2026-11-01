from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock
from translator_bot.translation import LocalTranslator, TranslationUnavailable
from translator_bot.translation_policy import actor_drift, effective_context
from translator_bot.name_rendering import localized_name, name_bindings
from translator_bot.translation import PROTECTED_PATTERN


class PartialPhraseCacheTests(unittest.TestCase):
    def test_reviewed_multiline_learning_uses_shared_lexical_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache=Path(temporary)/'cache.sqlite3'
            t=LocalTranslator('http://localhost',cache_path=cache)
            t.remember_reviewed('Thank you.\nI will bring tea.','en','ru','Спасибо.\nЯ принесу чай.')
            restarted=LocalTranslator('http://localhost',cache_path=cache)
            restarted._request=Mock(side_effect=AssertionError('No model for an approved phrase'))
            self.assertEqual(restarted.cached_translation('Thank you!! ❤️','en','ru').text,'Спасибо!! ❤️')
            self.assertIsNone(restarted.cached_translation('I will bring tea.','en','ru'))
            restarted._request.assert_not_called()

    def test_generation_cannot_replace_reviewed_phrase_across_processes(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache=Path(temporary)/'cache.sqlite3'
            stale=LocalTranslator('http://localhost',cache_path=cache)
            _,token=stale._get_fragment('Thank you','en','ru',count=False)
            stale._put_fragment(token,'Благодарю')
            reviewer=LocalTranslator('http://localhost',cache_path=cache)
            reviewer.remember_reviewed('Thank you.','en','ru','Спасибо.')
            self.assertFalse(stale._put_fragment(token,'Благодарю'))
            self.assertEqual(stale.cached_translation('Thank you!','en','ru').text,'Спасибо!')
            # An affected flag still invalidates reviewed entries normally.
            reviewer.invalidate_source('Thank you!')
            self.assertIsNone(stale.cached_translation('Thank you!','en','ru'))

    def test_reviewed_precedence_also_works_without_persistent_cache(self):
        t=LocalTranslator('http://localhost')
        t.remember_reviewed('Thank you.','en','ru','Спасибо.')
        _,token=t._get_fragment('Thank you','en','ru',count=False)
        self.assertFalse(t._put_fragment(token,'Благодарю'))
        self.assertEqual(t.cached_translation('Thank you!','en','ru').text,'Спасибо!')

    def test_original_thank_you_storage_lookup_mismatch_is_closed(self):
        t=LocalTranslator('http://localhost')
        def respond(prompt,format_schema):
            fields=json.loads(prompt.split('INPUT: ',1)[1])
            mapping={'Thank you.':'Спасибо.','I will bring tea.':'Я принесу чай.'}
            return json.dumps({key:mapping[fields[key]] for key in format_schema['properties']})
        t._request=Mock(side_effect=respond)
        t._translate_layout('Thank you.\nI will bring tea.','en','ru')
        before=t._request.call_count
        self.assertEqual(t.cached_translation('Thank you! ❤️','en','ru').text,'Спасибо! ❤️')
        self.assertEqual(t._request.call_count,before)

    def test_layout_learning_and_punctuation_variant_survive_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache=Path(temporary)/'cache.sqlite3'
            t=LocalTranslator('http://localhost',cache_path=cache)
            t._request=Mock(return_value=json.dumps({'part_0':'Нет.','part_1':'Я принесу чай.'}))
            self.assertEqual(t._translate_layout('No.\nI will bring tea.','en','ru'),'Нет.\nЯ принесу чай.')
            restarted=LocalTranslator('http://localhost',cache_path=cache)
            restarted._request=Mock(side_effect=AssertionError('All phrase variants should be cached'))
            with restarted.conversation_context(json.dumps({'originals':[{'text':'Entirely different history'}]})):
                self.assertEqual(restarted.cached_translation('No?? ❤️','en','ru').text,'Нет?? ❤️')
            restarted._request.assert_not_called()

    def test_partial_hit_omits_only_known_phrase_from_joint_inference(self):
        t=LocalTranslator('http://localhost')
        t.remember_reviewed('No.','en','ru','Нет.')
        t._request=Mock(return_value=json.dumps({'part_1':'Она уже ушла.'}))
        text='No! ❤️\nShe has already left.'
        self.assertEqual(t._translate_layout(text,'en','ru'),'Нет! ❤️\nОна уже ушла.')
        schema=t._request.call_args.kwargs['format_schema']
        self.assertEqual(schema['required'],['part_1'])
        self.assertIn('She has already left.',t._request.call_args.args[0])
        self.assertGreaterEqual(t.cache_stats()['fragment_hits'],1)

    def test_emoji_field_learns_shared_core_not_placeholder_identity(self):
        t=LocalTranslator('http://localhost')
        t._request=Mock(return_value=json.dumps({'part_0':'Нет! [[DCTOKEN_0]]','part_1':'Я принесу чай.'}))
        t._translate_layout('No! ❤️\nI will bring tea.','en','ru')
        self.assertEqual(t.cached_translation('No...? 😄','en','ru').text,'Нет...? 😄')

    def test_partial_hit_keeps_distinct_markers_in_other_fields(self):
        t=LocalTranslator('http://localhost')
        t.remember_reviewed('No.','en','ru','Нет.')
        t._request=Mock(return_value=json.dumps({'part_1':'Я принесу чай. [[DCTOKEN_1]]'}))
        self.assertEqual(t._translate_layout('No! ❤️\nI will bring tea. 🙈','en','ru'),
                         'Нет! ❤️\nЯ принесу чай. 🙈')

    def test_flag_during_inference_cannot_repopulate_phrase(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache=Path(temporary)/'cache.sqlite3'
            t=LocalTranslator('http://localhost',cache_path=cache)
            other=LocalTranslator('http://localhost',cache_path=cache)
            def response(*args,**kwargs):
                other.invalidate_source('No!')
                return json.dumps({'part_0':'Нет.','part_1':'Я принесу чай.'})
            t._request=Mock(side_effect=response)
            t._translate_layout('No.\nI will bring tea.','en','ru')
            self.assertIsNone(t.cached_translation('No??','en','ru'))

    def test_contextual_clause_does_not_become_portable_with_partial_cache(self):
        t=LocalTranslator('http://localhost')
        t.remember_reviewed('No.','en','ru','Нет.')
        t._request=Mock(return_value=json.dumps({'part_1':'Я принесу биту.'}))
        with t.conversation_context(json.dumps({'originals':[{'text':'Baseball practice'}]})):
            t._translate_layout('No.\nI will bring a bat.','en','ru')
        with t.conversation_context(json.dumps({'originals':[{'text':'Rescue an injured flying mammal'}]})):
            self.assertIsNone(t.cached_translation('No.\nI will bring a bat.','en','ru'))

    def test_unfinished_no_determiner_is_never_replayed_as_negative_answer(self):
        t=LocalTranslator('http://localhost')
        t.remember_reviewed('No.','en','ru','Нет.')
        t._request=Mock(return_value=json.dumps({'part_0':'Никаких','part_1':'яблок для меня.'}))
        self.assertEqual(t._translate_layout('No\napples for me.','en','ru'),'Никаких\nяблок для меня.')
        self.assertIn('part_0',t._request.call_args.kwargs['format_schema']['required'])
        self.assertEqual(t.cached_translation('No??','en','ru').text,'Нет??')
        t.remember_reviewed('No\napples for me.','en','ru','Никаких\nяблок для меня.')
        self.assertEqual(t.cached_translation('No??','en','ru').text,'Нет??')

    def test_ru_wellbeing_question_only_reuses_in_safe_direction(self):
        t=LocalTranslator('http://localhost')
        t.remember_reviewed('Как вы себя чувствуете?','ru','en','How are you feeling?')
        with t.conversation_context(json.dumps({'originals':[{'text':'Different person and topic'}]})):
            self.assertEqual(t.cached_translation('Как вы себя чувствуете?? ❤️','ru','en').text,'How are you feeling?? ❤️')
        self.assertNotEqual(effective_context('How are you feeling?',json.dumps({'originals':[{'text':'Formal addressee'}]}),'en','ru'),'')

    def test_possessive_picture_is_not_a_false_actor_reversal(self):
        source='I have a picture of you running.'
        self.assertFalse(actor_drift(source,'У меня в голове картинка, как ты бежишь.','en','ru'))
        self.assertTrue(actor_drift('I have a picture.','У тебя есть картинка.','en','ru'))

    def test_negative_auxiliaries_preserve_mixed_explicit_and_implicit_roles(self):
        for auxiliary in ("wouldn't","couldn't","don't","didn't","won't","can't","shouldn’t"):
            with self.subTest(auxiliary=auxiliary):
                self.assertFalse(actor_drift('Убирал, чтобы ты не видел беспорядок.',
                    f'I was tidying so you {auxiliary} see the mess.','ru','en'))
        self.assertFalse(actor_drift('Работал, пока ты не слышал музыку.',
            'I was working while you heard no music.','ru','en'))
        self.assertTrue(actor_drift('Ты любишь меня.','I love you.','ru','en'))
        self.assertTrue(actor_drift('Я тебя не слышал.',"You couldn't hear me.",'ru','en'))

    def test_known_name_cases_are_names_not_phonetic_english_suffixes(self):
        self.assertEqual(localized_name('Анну','ru','en'),'Anna')
        self.assertEqual(localized_name('Алисе','ru','en'),'Alice')
        self.assertEqual(localized_name('Адду','ru','en'),'Adda')
        self.assertEqual(name_bindings('Я слушаю Анну.','ru','en',PROTECTED_PATTERN),{'Анну':'Anna'})
        self.assertIsNone(localized_name('Траву','ru','en'))
        self.assertEqual(name_bindings('`Анну` @Адду https://example.org/Алисе','ru','en',PROTECTED_PATTERN),{})

    def test_asap_idiom_does_not_invent_a_condition(self):
        t=LocalTranslator('http://localhost')
        self.assertIn('standalone colloquial idiom',t._prompt('Как только, так сразу','ru','en'))
        self.assertNotIn('standalone colloquial idiom',t._prompt('Как только он придёт, так сразу начнём.','ru','en'))

    def test_cat_sounds_use_no_model_and_are_not_general_word_rewrites(self):
        t=LocalTranslator('http://localhost')
        t._request=Mock(side_effect=AssertionError('No inference for a cat sound'))
        self.assertEqual(t.translate_to('Meaw','ru','en').text,'Мяу')
        self.assertEqual(t.translate_to('Meow! ❤️','ru','en').text,'Мяу! ❤️')
        self.assertEqual(t.translate_to('Мяу!','en','ru').text,'Meow!')
        for text in ('I heard a meow.','@Meaw','`meow`','https://example.org/meow','meadow','mewling'):
            self.assertIsNone(t._cat_sound(text,'en','ru'))
        t._request.assert_not_called()
