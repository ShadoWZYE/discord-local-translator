import unittest
from unittest.mock import Mock
from translator_bot.translation import LocalTranslator,TranslationUnavailable,direction_for


class SourceFidelityAuditTests(unittest.TestCase):
    def test_explicit_register_omission_not_a_moderation_decision(self):
        for source,bad in [('We speak the same fucking language.','Мы говорим на одном языке.'),
                ('This is fucked. Fucking hell.','Это пиздец. Черт побери.'),
                ('She is so damn incredible.','Она просто невероятная.')]:
            with self.assertRaises(TranslationUnavailable):
                LocalTranslator._validate_output(source,bad,'ru','en')
        LocalTranslator._validate_output('We speak the same fucking language.','Мы говорим на одном ёбаном языке.','ru','en')
        LocalTranslator._validate_output('She is so damn incredible.','Она такая чертовски невероятная.','ru','en')
        LocalTranslator._validate_output('The word "fucking" is vulgar.','Слово "fucking" нецензурное.','ru','en')
        LocalTranslator._validate_output('Code `fucking` is an identifier.','Код `fucking` — идентификатор.','ru','en')
        # Do not demand or insert swearing absent from the source.
        LocalTranslator._validate_output('This is broken.','Это сломано.','ru','en')

    def test_mood_must_not_become_the_person_fitting_the_object(self):
        source='This is ours, whether I am in a mood fitting it or not.'
        with self.assertRaises(TranslationUnavailable):
            LocalTranslator._validate_output(source,'Это наше, подхожу ли я ему или нет.','ru','en')
        LocalTranslator._validate_output(source,'Это наше, независимо от того, подходящее ли у меня настроение.','ru','en')

    def test_physical_level_bases_and_real_databases_are_distinct(self):
        with self.assertRaises(TranslationUnavailable):
            LocalTranslator._validate_output('Это базы 12 уровня.','These are level 12 databases.','en','ru')
        LocalTranslator._validate_output('Это базы 12 уровня.','These are level 12 bases.','en','ru')
        LocalTranslator._validate_output('База данных содержит 12 таблиц.','The database contains 12 tables.','en','ru')

    def test_known_name_and_tilde_take_no_model_call(self):
        t=LocalTranslator('http://localhost')
        t._request=Mock(side_effect=AssertionError('Known name does not need inference'))
        self.assertEqual(t.translate_to('Adda~','ru','en').text,'Адда~')
        self.assertEqual(t.translate_to('Адда~','en','ru').text,'Adda~')
        self.assertEqual(direction_for('Adda~','auto'),('en','ru'))
        t._request.assert_not_called()

    def test_number_and_duration_guidance_is_source_scoped(self):
        t=LocalTranslator('http://localhost')
        self.assertIn('inherit I, not we',t._prompt("I'm with you, improving the notes.",'en','ru'))
        self.assertNotIn('inherit I, not we',t._prompt('We are improving the notes.','en','ru'))
        self.assertIn('FOR a minute',t._prompt('Ты мне нужен в игре на минуту.','ru','en'))
        self.assertNotIn('FOR a minute',t._prompt('Он появится через минуту.','ru','en'))


if __name__=='__main__':unittest.main()
