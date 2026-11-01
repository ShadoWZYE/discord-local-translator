import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from translator_bot.translation import LocalTranslator,TranslationUnavailable
from translator_bot.translation_policy import actor_drift,independent_unit


class AuditRegressionTests(unittest.TestCase):
    def test_implicit_first_person_then_explicit_question_is_not_reversal(self):
        source='В целом неплохо, без особого настроения. Как ты себя чувствуешь?'
        good="I'm doing okay overall, just not in any particular mood. How are you feeling?"
        self.assertFalse(actor_drift(source,good,'ru','en'))
        LocalTranslator._validate_participant_roles(source,good,'ru','en')
        self.assertTrue(actor_drift('Как ты себя чувствуешь?',"How am I feeling?",'ru','en'))
        self.assertTrue(actor_drift('How are you feeling?','Как я себя чувствую?','en','ru'))

    def test_instructions_may_not_leak_but_actual_quoted_labels_are_allowed(self):
        with self.assertRaisesRegex(TranslationUnavailable,'instructions leaked'):
            LocalTranslator._validate_output('Я потянулась.', 'I stretched. CURRENT SPEAKER (I/я), not the addressee','en','ru')
        LocalTranslator._validate_output('The label is CURRENT SPEAKER (I/я).','The label is CURRENT SPEAKER (I/я).','en','en')

    def test_structured_source_is_last_not_followed_by_actor_instructions(self):
        t=LocalTranslator('http://localhost')
        t._request=Mock(return_value=json.dumps({'part_0':'Прости.','part_2':'Я потянулась.'}))
        t.translate_to('Sorry.\n\nI stretched.','ru','en')
        prompt=t._request.call_args.args[0]
        fields=json.loads(prompt.rsplit('INPUT: ',1)[1])
        self.assertEqual(fields['part_2'],'I stretched.')
        self.assertLess(prompt.index('EXPLICIT SOURCE ACTORS'),prompt.rindex('INPUT: '))

    def test_stretched_greetings_are_deterministic_not_arbitrary_word_normalization(self):
        for source,target,text,expected in (('en','ru','hellooo','привееет'),
                ('en','ru','HELLOOO! ❤️','ПРИВЕЕЕТ! ❤️'),
                ('ru','en','привееет','hellooo')):
            t=LocalTranslator('http://localhost')
            t._request=Mock(side_effect=AssertionError('No inference needed'))
            self.assertEqual(t.translate_to(text,target,source).text,expected)
            self.assertTrue(independent_unit(text,source,target))
        for text in ('hell','helloooAlice','`hellooo`','hellooo https://example.com'):
            self.assertIsNone(LocalTranslator._fixed_chat(text,'en','ru'))

    def test_short_wrong_script_and_gratitude_reversal_are_rejected(self):
        for source,output,src,target in (('hellooo','hellooo.','en','ru'),
                ('HELLOOO','HELLOOO','en','ru'),('ДА','ДА','ru','en'),
                ('да','да','ru','en'),('[Спасибо, что я тут]','[Sorry I am here]','ru','en')):
            with self.assertRaises(TranslationUnavailable):
                LocalTranslator._validate_output(source,output,target,src)
        LocalTranslator._validate_output('Use VPN','VPN','ru','en')

    def test_cache_miss_reasons_and_reviewed_write_survive_process_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cache.sqlite3'
            t=LocalTranslator('http://localhost',cache_path=path)
            with t.conversation_context('different contexts are not interchangeable'):
                t.remember_reviewed('Thank you!','en','ru','Спасибо!')
            other=LocalTranslator('http://localhost',cache_path=path)
            with other.conversation_context(json.dumps({'originals':[{'text':'Different context'}]})):
                self.assertEqual(other.cached_translation('Thank you!','en','ru').text,'Спасибо!')
                self.assertEqual(other.cache_stats()['lookup_disk_hits'],1)
                self.assertEqual(other.cache_stats()['model_attempts'],0)
            context_text='She agreed.'
            self.assertEqual(other._disk_cache.miss_reason(b'new',other._source_key(context_text)),'cold')
            other._disk_cache.put(b'other','Она согласилась.',(0,0),other._source_key(context_text))
            self.assertEqual(other._disk_cache.miss_reason(b'new',other._source_key(context_text)),'other_key')
            other.invalidate_source(context_text)
            self.assertEqual(other._disk_cache.miss_reason(b'new',other._source_key(context_text)),'invalidated')
            self.assertEqual(other.remember_reviewed('She agreed.','en','ru','Она согласилась.'),0)

    def test_past_reason_is_not_hypothetical_but_future_threat_still_is(self):
        with self.assertRaises(TranslationUnavailable):
            LocalTranslator._validate_participant_roles('А то я кудрявая стала.','Otherwise, I became curly.','ru','en')
        LocalTranslator._validate_participant_roles('А то я разозлюсь.','Otherwise, I will get angry.','ru','en')
        LocalTranslator._validate_participant_roles('А то я бы разозлилась.','Otherwise, I would have got angry.','ru','en')
        source='А то я кудрявая стала. А то я разозлюсь.'
        protected,values=LocalTranslator._protect(source,'ru','en')
        self.assertIn('You see,',values.values())
        self.assertIn('А то я разозлюсь.',protected)
        self.assertNotIn('Otherwise',values.values())

    def test_voice_referent_constraint_is_grounded_and_not_applied_to_actual_man(self):
        t=LocalTranslator('http://localhost')
        context=json.dumps({'originals':[{'text':'Полное погружение в твой голос.'}]})
        with t.conversation_context(context):
            with self.assertRaises(TranslationUnavailable):
                t._validate_reference_roles('Я думаю, что он слышится некрасиво.',"I think his voice sounds unpleasant.")
            t._validate_reference_roles('Я думаю, что он слышится некрасиво.',"I think it doesn't sound nice.")
            t._validate_reference_roles('Мой брат говорит, что он слышится некрасиво.',"My brother says he sounds unpleasant.")
            t._validate_reference_roles('Мне нравится его голос, он слышится красиво.',"I like his voice; it sounds nice.")
        protected,values=LocalTranslator._protect('Мне не то, что не нравится это слышать.','ru','en')
        self.assertNotIn("It's not that I don't like",values.values())
        self.assertIn('Мне не то, что не нравится',protected)  # Do not hide complement grammar in opaque tokens.
        self.assertIn('это слышать.',protected)
        self.assertIn('NEGATION SCOPE:',t._prompt(protected,'ru','en'))
        self.assertNotIn("It's not that I don't like",LocalTranslator._protect('Мне не только не нравится это слышать.','ru','en')[1].values())
        with self.assertRaises(TranslationUnavailable):
            LocalTranslator._validate_participant_roles('Мне не то, что не нравится это слышать.',"It's not just that I don't like hearing it.",'ru','en')


if __name__=='__main__':unittest.main()
