import unittest
from translator_bot.translation import is_phonetic_sample, direction_for, LocalTranslator, language_payload


class PhoneticRosterTests(unittest.TestCase):
    def test_sound_samples_are_not_invented_prose_but_words_still_translate(self):
        for text in ('Ț ~ Ș','Țî','Ș / Ț'):
            self.assertTrue(is_phonetic_sample(text))
            self.assertIsNone(direction_for(text,'auto'))
        for text in ('și','Da','Я','我','Știu','Ține bine','Ț = ts'):
            self.assertFalse(is_phonetic_sample(text),text)

    def test_roster_handles_do_not_choose_the_prose_language(self):
        text='[TAG] RR\nHQ LeadName; 2IC NextName\n\nA Иван/الفدرالي: hold until the gate opens\nB Sara/Jane: move to the middle when safe\nP1 Алиса/العربي\nReserves: Андрей/OtherName\nEnter when the seat becomes free.'
        self.assertEqual(direction_for(text,'auto'),('en','ru'))
        self.assertNotIn('الفدرالي',language_payload(text))
        _,tokens=LocalTranslator._protect(text,'en','ru')
        for name in ('Иван','الفدرالي','Jane','Алиса','العربي','Андрей','LeadName','NextName'):
            self.assertIn(name,tokens.values())

    def test_short_yes_line_cannot_repeat_next_clause(self):
        from translator_bot.translation import TranslationUnavailable
        with self.assertRaises(TranslationUnavailable):
            LocalTranslator._validate_layout_overlap('Да\nЭто худший день.',"Yes, this is the worst day.\nThis is the worst day.")
        LocalTranslator._validate_layout_overlap('Да\nЭто худший день.','Yes\nThis is the worst day.')
