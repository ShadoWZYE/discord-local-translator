import unittest
from translator_bot.translation import LocalTranslator,TranslationUnavailable


class SurpriseGuidanceTests(unittest.TestCase):
    def test_profane_surprise_and_discovery_tag_guidance_is_specific(self):
        t=LocalTranslator('http://localhost')
        self.assertIn('strongly profane surprise',t._prompt('Holy fucking shit!','en','ru'))
        self.assertNotIn('strongly profane surprise',t._prompt('The holy water spilled.','en','ru'))
        self.assertIn('standalone discovery tag',t._prompt('The more you know, huh?','en','ru'))
        self.assertNotIn('standalone discovery tag',t._prompt('The more you know, the better.','en','ru'))

    def test_guidance_does_not_relax_explicit_profanity_validation(self):
        with self.assertRaises(TranslationUnavailable):
            LocalTranslator._validate_explicit_content('Holy fucking shit!','Чёрт побери!','en','ru')
        LocalTranslator._validate_explicit_content('Holy fucking shit!','Ебать, пиздец!','en','ru')

    def test_complete_conventional_exclamations_render_locally_not_partial_prose(self):
        t=LocalTranslator('http://localhost')
        self.assertEqual(t._fixed_chat('Holy fucking shit...','en','ru'),'Ебать, пиздец...')
        self.assertEqual(t._fixed_chat('The more you know eh?','en','ru'),'Чего только не узнаешь, а?')
        self.assertEqual(t._fixed_chat('HOLY FUCKING SHIT!','en','ru'),'ЕБАТЬ, ПИЗДЕЦ!')
        for text in ('The holy water spilled.','The more you know, the better.',
                     'He said "Holy fucking shit!"','Holy fucking shit, the bus moved.'):
            self.assertIsNone(t._fixed_chat(text,'en','ru'))
