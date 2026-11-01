import json
import unittest
from unittest.mock import Mock
from translator_bot.translation import LocalTranslator,TranslationUnavailable


class FeedbackClassesTests(unittest.TestCase):
    def setUp(self):
        self.t=LocalTranslator('http://localhost')

    def test_nested_borrower_roles_reject_reversal_but_allow_genuine_reciprocity(self):
        source='Я думаю, что ты у меня заимствуешь время.'
        with self.assertRaises(TranslationUnavailable):
            self.t._validate_participant_roles(source,"I think I'm borrowing your time.",'ru','en')
        self.t._validate_participant_roles(source,"I think you're borrowing time from me.",'ru','en')
        self.t._validate_participant_roles('Ты у меня берешь время, а я у тебя занимаю деньги.',
            'You take my time and I borrow your money.','ru','en')

    def test_local_clock_is_not_invented_availability(self):
        with self.assertRaises(TranslationUnavailable):
            self.t._validate_participant_roles('У неё будет 5 часов утра.',"She'll be available at 5 AM.",'ru','en')
        self.t._validate_participant_roles('У неё будет 5 часов утра.',"It will be 5 AM where she is.",'ru','en')
        self.t._validate_participant_roles('Она будет свободна, у неё будет 5 часов утра.',
            "She'll be free at 5 AM her time.",'ru','en')

    def test_emoji_predicate_stays_implicit_not_censored_or_reinterpreted(self):
        source='Если бы я была в них, то давно бы 💦💦💦'
        with self.assertRaises(TranslationUnavailable):
            self.t._validate_participant_roles(source,"If I had been wearing them, I would have realized it long ago. 💦💦💦",'ru','en')
        self.t._validate_participant_roles(source,"If I'd been wearing them, I'd have already 💦💦💦",'ru','en')
        self.t._validate_participant_roles('Я бы давно поняла. 💦','I would have understood ages ago. 💦','ru','en')

    def test_laughter_is_restored_without_putting_phonetic_string_in_prompt(self):
        laughter='Ахаахаххахахахахахха'
        self.t._request=Mock(return_value=json.dumps({'part_1':'No, not an idiot. Worse, much worse….'}))
        result=self.t.translate_to(laughter+'\nНет, не идиот. Хуже, намного хуже….','en','ru')
        self.assertEqual(result.text,laughter.translate(str.maketrans('ахАХ','ahAH'))+'\nNo, not an idiot. Worse, much worse….')
        self.t._request.assert_called_once()
        prompt=self.t._request.call_args.args[0]
        self.assertNotIn(laughter,prompt)
        self.assertNotIn(laughter.translate(str.maketrans('ахАХ','ahAH')),prompt)
        self.assertIn('laughter already rendered',prompt)

    def test_vulgar_idioms_have_source_guidance_not_a_profanity_filter(self):
        prompt=self.t._prompt('Сказочный долбоеб.','ru','en')
        self.assertIn('monumental',prompt)
        self.assertIn('fuckwit',prompt)
        self.assertNotIn('monumental',self.t._prompt('Сказочный дворец.','ru','en'))
        prompt=self.t._prompt('Egocentric moron and cocksucker. A fucking retard. Waste of oxygen.','en','ru')
        for word in ('хуесос','ебаный дебил','эгоцентричный','кислорода'):
            self.assertIn(word,prompt)
        # No output word-count/profanity classifier vetoes ordinary translation.
        self.t._validate_output('I am a moron.','Я придурок.','ru','en')

    def test_future_surprise_question_guidance_is_narrow(self):
        self.assertIn('Is that how many',self.t._prompt('Это столько подарков меня ждёт, если встретимся?','ru','en'))
        self.assertNotIn('Is that how many',self.t._prompt('Я жду поцелуя.','ru','en'))
