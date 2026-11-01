import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from translator_bot.translation import LocalTranslator, TranslationUnavailable, TranslationResult
from translator_bot.translation_policy import actor_drift, effective_context, independent_unit, layout_units


class RobustPolicyTests(unittest.TestCase):
    def test_explicit_people_cannot_be_overridden_by_history(self):
        for source,bad in (("So, I guess you're right?",'Значит, я права?'),
                           ("I'm doing it on purpose.",'Ты делаешь это специально.'),
                           ('You are doing it on purpose.','Я делаю это специально.'),
                           ('He knows.','Она знает.'),('She knows.','Он знает.')):
            self.assertTrue(actor_drift(source,bad,'en','ru'))
            with self.assertRaises(TranslationUnavailable):
                LocalTranslator._validate_participant_roles(source,bad,'en','ru')

    def test_legitimate_objects_implicit_people_and_dative_are_allowed(self):
        for source,good in (("I want you here.",'Я хочу, чтобы ты был здесь.'),
                            ('I like you.','Ты мне нравишься.'),
                            ("I know.",'Знаю.'),
                            ('I hope you slept.','Надеюсь, ты спал.')):
            self.assertFalse(actor_drift(source,good,'en','ru'),(source,good))
        self.assertFalse(actor_drift('Надеюсь, ты спал.','I hope you slept.','ru','en'))
        self.assertFalse(actor_drift('У меня есть машина. Она красная.',"I have a car. It's red.",'ru','en'))
        self.assertFalse(actor_drift('Она мне нравится.','I like her.','ru','en'))

    def test_dependent_phrases_keep_context(self):
        for source,code in (('She loves him.','en'),('Я знаю это.','ru'),('If you','en'),('I was tired.','en'),
                            ('You can bring coffee.','en'),('I will call you.','en')):
            self.assertFalse(independent_unit(source,code,'ru' if code=='en' else 'en'))

    def test_context_projection_and_regeneration(self):
        a=json.dumps({'current_speaker':{'id':1,'name':'A'},'originals':[]})
        b=json.dumps({'current_speaker':{'id':100,'name':'A'},'originals':[]})
        self.assertEqual(effective_context('It happened.',a,'en','ru'),effective_context('It happened.',b,'en','ru'))
        self.assertEqual(effective_context('Thank you!',a,'en','ru'),'')
        self.assertIn('request',effective_context('Thank you!',json.dumps({'request':'regenerate'}),'en','ru'))

    def test_repeated_greetings_ignore_irrelevant_changing_history(self):
        t=LocalTranslator('http://localhost')
        t._translate_uncached=Mock(return_value=TranslationResult('en','ru','Спасибо!'))
        for history in ('one','two'):
            with t.conversation_context(json.dumps({'originals':[{'text':history}]})):
                t.translate_to('Thank you!','ru','en')
        t._translate_uncached.assert_called_once()

    def test_only_new_fields_generated_with_all_fields_as_context(self):
        t=LocalTranslator('http://localhost')
        t._request=Mock(side_effect=[json.dumps({'part_0':'Спасибо!','part_1':'Если ты'}),
                                    json.dumps({'part_1':'Скажи мне почему?'})])
        first=t.translate_to('Thank you!\nIf you','ru','en')
        second=t.translate_to('Thank you!\nTell me why?','ru','en')
        self.assertEqual(first.text,'Спасибо!\nЕсли ты')
        self.assertEqual(second.text,'Спасибо!\nСкажи мне почему?')
        self.assertEqual(t._request.call_args.kwargs['format_schema']['required'],['part_1'])
        self.assertIn('Thank you!',t._request.call_args.args[0])
        self.assertEqual(t.cache_stats()['fragment_hits'],1)

    def test_fragment_revision_persists_and_parent_invalidation_blocks_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cache.sqlite3'
            a=LocalTranslator('http://localhost',cache_path=path)
            _,token=a._get_fragment('Thank you!','en','ru')
            a._put_fragment(token,'Спасибо!')
            b=LocalTranslator('http://localhost',cache_path=path)
            self.assertEqual(b._get_fragment('Thank you!','en','ru')[0],'Спасибо!')
            a.invalidate_source('Thank you!\nIf you')
            b._put_fragment(token,'Устаревшее')
            self.assertIsNone(b._get_fragment('Thank you!','en','ru')[0])

    def test_brackets_spoilers_and_underscores_are_protected_inline(self):
        t=LocalTranslator('http://localhost')
        source='[An aside]\n_Quietly_ ||a secret||'
        protected,values=t._protect(source,'en','ru',preserve_quotes=True)
        self.assertEqual(t._restore(protected,values),source)
        for syntax in ('[',']','_','||'):
            self.assertIn(syntax,values.values())

    def test_profanity_guidance_preserves_intensity_without_censor_gate(self):
        prompt=LocalTranslator('http://localhost')._prompt("This is fucked. Damn pronouns.",'en','ru')
        self.assertIn('пиздец',prompt)
        self.assertIn('Damn is weaker',prompt)

    def test_long_paragraph_sentences_preserve_exact_spaces_and_newlines(self):
        source='I will bring coffee.  I will bring sandwiches!\t\tYou can bring plates?\nHello!'
        parts,separators=layout_units(source,sentences=True)
        self.assertEqual(parts,['I will bring coffee.','I will bring sandwiches!','You can bring plates?','Hello!'])
        self.assertEqual(''.join(p+(separators[i] if i<len(separators) else '') for i,p in enumerate(parts)),source)
        text='You can call Prof. Smith. You can bring coffee.'
        self.assertEqual(layout_units(text,sentences=True)[0],['You can call Prof. Smith.','You can bring coffee.'])

    def test_all_cached_fields_need_no_inference_and_keep_layout(self):
        t=LocalTranslator('http://localhost')
        for source,value in (('Thank you!','Спасибо!'),('Good night!','Спокойной ночи!')):
            _,token=t._get_fragment(source,'en','ru')
            t._put_fragment(token,value)
        t._request=Mock(side_effect=AssertionError('No new prose'))
        self.assertEqual(t.translate_to('  Thank you!\n\nGood night!  ','ru','en').text,'  Спасибо!\n\nСпокойной ночи!  ')

    def test_parent_invalidation_also_clears_short_cached_sentence_in_long_message(self):
        t=LocalTranslator('http://localhost')
        _,token=t._get_fragment('I will bring coffee.','en','ru')
        t._put_fragment(token,'Я принесу кофе.')
        t.invalidate_source('I will bring coffee. '+('I will bring sandwiches. '*15))
        self.assertIsNone(t._get_fragment('I will bring coffee.','en','ru')[0])

    def test_prompt_context_projection_uses_unprotected_original(self):
        t=LocalTranslator('http://localhost')
        t._context.source_text='Thank you!'
        with t.conversation_context(json.dumps({'originals':[{'text':'UNRELATED PRIVATE HISTORY'}]})):
            self.assertNotIn('UNRELATED PRIVATE HISTORY',t._prompt('[[DCTOKEN_0]]','en','ru'))

    def test_fast_lookup_assembles_cached_units_without_gpu_work(self):
        t=LocalTranslator('http://localhost')
        for source,value in (('Thank you!','Спасибо!'),('Good night!','Спокойной ночи!')):
            _,token=t._get_fragment(source,'en','ru')
            t._put_fragment(token,value)
        self.assertEqual(t.cached_translation('Thank you!\nGood night!','en','ru').text,'Спасибо!\nСпокойной ночи!')
        hits=t.cache_stats()['fragment_hits']
        self.assertIsNone(t.cached_translation('Thank you!\nAn uncached fragment','en','ru'))
        self.assertEqual(t.cache_stats()['fragment_hits'],hits)

    def test_code_patch_and_unrelated_glossary_do_not_cold_start_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary=Path(tmp)/'glossary.json'
            glossary.write_text('{"en":{"coffee":"кофе"}}',encoding='utf-8')
            t=LocalTranslator('http://localhost',glossary_path=glossary)
            key=t._cache_fingerprint('I will bring coffee.','en','ru')
            t._policy_code=b'arbitrary unrelated bot patch'
            glossary.write_text('{"en":{"coffee":"кофе","sandwiches":"бутерброды"}}',encoding='utf-8')
            self.assertEqual(key,t._cache_fingerprint('I will bring coffee.','en','ru'))
            glossary.write_text('{"en":{"coffee":"кофейный напиток"}}',encoding='utf-8')
            self.assertNotEqual(key,t._cache_fingerprint('I will bring coffee.','en','ru'))

    def test_semantic_rule_revision_affects_matching_sources_only(self):
        t=LocalTranslator('http://localhost')
        before=t._cache_fingerprint('I will bring coffee.','en','ru')
        unrelated=t._cache_fingerprint('Thank you!','en','ru')
        from translator_bot.translation_policy import CACHE_RULES
        rules=tuple(('actors-v2' if name=='actors-v1' else name,pattern) for name,pattern in CACHE_RULES)
        with patch('translator_bot.translation_policy.CACHE_RULES',rules):
            self.assertNotEqual(before,t._cache_fingerprint('I will bring coffee.','en','ru'))
            self.assertEqual(unrelated,t._cache_fingerprint('Thank you!','en','ru'))

    def test_new_editorial_brackets_and_removed_asides_fail_closed(self):
        for source,output in (('Собирайте меня.','Collect [me].'),('[Это отступление]','This is an aside')):
            with self.assertRaises(TranslationUnavailable):
                LocalTranslator._validate_output(source,output,'en','ru')

    def test_current_validation_rejects_only_bad_cached_value_not_other_rows(self):
        t=LocalTranslator('http://localhost')
        self.assertFalse(t._cached_valid("I'm doing it on purpose.",'Ты делаешь это специально.','en','ru'))
        self.assertFalse(t._cached_valid('Первая строка\nВторая строка','First line. Second line.','ru','en'))
        self.assertTrue(t._cached_valid('Спасибо!','Thank you!','ru','en'))


if __name__=='__main__':
    unittest.main()
