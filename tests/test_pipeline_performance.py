from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock
from translator_bot.model_metrics import model_measurement
from translator_bot.translation import LocalTranslator,TranslationResult
from translator_bot.edit_translation import edit_window
from translator_bot.reuse_store import ReuseStore
from translator_bot.result_cache import ResultCache
from translator_bot.prompt_contract import CONTEXT_GUIDANCE
from translator_bot.translation_policy import effective_context


class RuntimeMetricsTests(unittest.TestCase):
    def test_missing_native_telemetry_is_not_zero_and_invalid_values_are_unknown(self):
        r=model_measurement({'prompt_eval_duration':2e9,'eval_duration':1e9},3,prompt_characters=100,structured=False)
        self.assertIsNone(r['native_cached_tokens']);self.assertEqual(r['prefill_seconds'],2)
        for v in (True,-1,float('nan'),float('inf'),'12'):
            self.assertIsNone(model_measurement({'prompt_eval_cached_count':v},0,prompt_characters=1,structured=False)['native_cached_tokens'])
        self.assertEqual(model_measurement({'prompt_eval_cached_count':0},0,prompt_characters=1,structured=False)['native_cached_tokens'],0)

    def test_request_captures_metadata_without_prompt_plaintext(self):
        with tempfile.TemporaryDirectory() as folder:
            t=LocalTranslator('http://localhost',cache_path=Path(folder)/'cache.db')
            response=Mock();response.json.return_value=dict(message={'content':'result'},prompt_eval_count=100,prompt_eval_cached_count=75,prompt_eval_duration=1e9,eval_duration=2e9)
            t._session.post=Mock(return_value=response)
            with t.request_observation(1,'private original','automatic',phase='translation'):
                t._request('private prompt')
            r=t.cache_stats()['recent_live_cache']['model_runtime']
            self.assertEqual(r['native_cached_tokens'],75);self.assertEqual(r['native_cache_samples'],1)
            self.assertEqual(r['prefill_seconds'],1);self.assertEqual(r['generation_seconds'],2)


class ShortEditTests(unittest.TestCase):
    def test_short_aligned_edit_reuses_contextual_neighbors_not_just_changed_line(self):
        t=LocalTranslator('http://localhost')
        old='\n'.join(f'Note {i}: keep supplies ready.' for i in range(7))
        new=old.replace('Note 3: keep','Note 3: hold')
        output='\n'.join(f'Строка {i}: держите припасы наготове.' for i in range(7))
        self.assertLess(len(new),400)
        self.assertEqual(edit_window(old,new,output,source='en',target='ru'),(2,5,2,5))
        t.translate_to=Mock(return_value=TranslationResult('en','ru','\n'.join(output.split('\n')[2:5])))
        snapshot=dict(source_language='en',target_language='ru',source_text=old,translated_text=output,policy=t.policy_fingerprint())
        r=t.translate_edit(snapshot,new,'en','ru')
        self.assertEqual(r.text,output);self.assertGreater(r.reused_characters,0)
        self.assertEqual(r.total_characters,len(new))
        self.assertEqual(t.translate_to.call_args.args[0],'\n'.join(new.split('\n')[2:5]))

    def test_short_edit_still_rejects_names_alignment_and_excessive_changed_work(self):
        old='\n'.join(f'Note {i}: keep supplies ready.' for i in range(7))
        self.assertIsNone(edit_window(old,old.replace('Note 3:','Alice 3:'),old,source='en',target='ru'))
        self.assertIsNone(edit_window(old,old.replace('Note 3:','Note 3: edited'),'unaligned',source='en',target='ru'))
        self.assertIsNone(edit_window(old,old.replace('supplies','water'),old,source='en',target='ru'))

    def test_context_guidance_is_identical_and_precedes_dynamic_metadata(self):
        t=LocalTranslator('http://localhost')
        text='This fucking queue is broken.'
        with t.conversation_context(json.dumps({'originals':[{'text':'A human fact.'}]})):
            prompt=t._prompt(text,'en','ru')
        self.assertEqual(prompt.count(CONTEXT_GUIDANCE),1)
        self.assertLess(prompt.index(CONTEXT_GUIDANCE),prompt.index('Translate from '))
        self.assertIn('A human fact.',prompt);self.assertIn('Retain every explicit profane',prompt)
        with t.conversation_context(json.dumps({'originals':[{'text':'Different fact.'}]})):
            other=t._prompt('Я проверю завтра.','ru','en')
        self.assertEqual(prompt.split('REFERENCE CONTEXT',1)[0],other.split('REFERENCE CONTEXT',1)[0])
        self.assertNotIn(CONTEXT_GUIDANCE,t._prompt(text,'en','ru'))

    def test_same_reference_prefix_survives_target_and_source_guidance_changes(self):
        t=LocalTranslator('http://localhost')
        context=json.dumps({'current_speaker':{'id':'1'},'originals':[
            {'speaker':{'id':'2'},'text':'The meeting is tomorrow.'}]})
        with t.conversation_context(context):
            first=t._prompt('This fucking queue is broken.','en','ru')
            second=t._prompt('Я принесу кофе.\nПосле обеда.','ru','en')
        self.assertEqual(first.split('Translate from ',1)[0],second.split('Translate from ',1)[0])
        reference=effective_context('This fucking queue is broken.',context,'en','ru')
        for prompt in (first,second):
            self.assertEqual(prompt.count(reference),1)
            self.assertLess(prompt.index('END REFERENCE CONTEXT.'),prompt.index('Translate from '))
            self.assertIn(CONTEXT_GUIDANCE,prompt)
        self.assertIn('Retain every explicit profane',first)
        self.assertIn('Я принесу кофе.\nПосле обеда.',second)
