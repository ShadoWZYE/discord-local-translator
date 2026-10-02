import tempfile
import sqlite3
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from translator_bot.languages import (parse_languages, reading_seconds, LANGUAGES,
    TRANSLATION_ERRORS, FLAG_LANGUAGES, COUNTRY_CODES_BY_LANGUAGE, SUBDIVISION_FLAGS, country_flag)
from translator_bot.storage import SettingsStore
from translator_bot.translation import LocalTranslator, TranslationUnavailable, detect_multilingual, direction_for


class LanguageTests(unittest.TestCase):
    def test_country_flags_are_unique_and_target_supported_languages(self):
        codes = [code for countries in COUNTRY_CODES_BY_LANGUAGE.values() for code in countries.split()]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(len(FLAG_LANGUAGES), len(codes) + 3)
        self.assertGreater(len(FLAG_LANGUAGES), 180)
        self.assertTrue(set(FLAG_LANGUAGES.values()) <= set(LANGUAGES))
        for code in codes:
            self.assertEqual(len(country_flag(code)), 2)
            self.assertTrue(all(0x1F1E6 <= ord(c) <= 0x1F1FF for c in country_flag(code)))

    def test_multilingual_country_defaults_and_aliases(self):
        for code, language in {'GB': 'en', 'CA': 'en', 'AU': 'en', 'SG': 'en',
                'BE': 'nl', 'CH': 'de', 'MD': 'ro', 'KZ': 'ru', 'BR': 'pt',
                'MX': 'es', 'DZ': 'ar', 'KP': 'ko', 'TW': 'zh'}.items():
            with self.subTest(country=code):
                self.assertEqual(FLAG_LANGUAGES[country_flag(code)], language)

    def test_subdivision_flags_use_exact_discord_unicode_sequences(self):
        for name, tag in {'England': 'gbeng', 'Scotland': 'gbsct', 'Wales': 'gbwls'}.items():
            flag = SUBDIVISION_FLAGS[name]
            self.assertEqual([ord(c) for c in flag], [0x1F3F4] +
                [0xE0000 + ord(c) for c in tag] + [0xE007F])
            self.assertEqual(FLAG_LANGUAGES[flag], 'en')
        for flag in ('🏴', '🏳️‍🌈', '🏴‍☠️', '🚩', '🇪🇺', '🇺🇳', '🇮🇳', '<:flag_gb:123>'):
            self.assertNotIn(flag, FLAG_LANGUAGES)

    def test_invalid_country_codes_are_rejected(self):
        for code in ('gb', 'GBR', '', '1A'):
            with self.subTest(code=code), self.assertRaises(ValueError):
                country_flag(code)

    def test_all_supported_source_languages_have_failure_notices(self):
        self.assertEqual(set(TRANSLATION_ERRORS), set(LANGUAGES))

    def test_conservative_timer(self):
        self.assertEqual(reading_seconds('Hello'), 90)
        self.assertEqual(reading_seconds('word ' * 200), 150)
        self.assertEqual(reading_seconds('word ' * 500), 330)
        self.assertEqual(reading_seconds('漢' * 400), 150)
        self.assertGreater(reading_seconds('longword' * 200), 90)

    def test_language_list_validation(self):
        self.assertEqual(parse_languages('English, Russian, Romanian, en'), ('en', 'ru', 'ro'))
        for invalid in ('en', 'en,en', 'en,unknown', ''):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_languages(invalid)

    def test_local_detection_and_multitarget_direction(self):
        text = 'Acesta este un mesaj în limba română, pentru a verifica traducerea.'
        self.assertEqual(detect_multilingual(text, ('en', 'ru', 'ro')), 'ro')
        self.assertEqual(direction_for(text, 'auto', ('en', 'ru', 'ro')), ('ro', 'en,ru'))
        self.assertIsNone(detect_multilingual('😀 https://example.com', tuple(LANGUAGES)))

    def test_short_english_clause_is_not_spanish_or_portuguese(self):
        self.assertEqual(detect_multilingual('I do so already.', tuple(LANGUAGES)), 'en')
        self.assertEqual(direction_for('I do so already.', 'auto', ('en', 'ru', 'es')), ('en', 'ru,es'))
        self.assertEqual(detect_multilingual('Я уже так делаю.', tuple(LANGUAGES)), 'ru')
        self.assertNotEqual(detect_multilingual('I do so already.', ('es', 'pt')), 'en')

    def test_language_settings_preserve_channel_enablement_and_user_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / 'test.db', {1: True, 2: False})
            store.set_channel_enabled(1, 20, True)
            store.set_channel_enabled(2, 21, False)
            store.set_user_mode(1, 40, 'off')
            store.set_languages(1, ('en', 'ru'))
            store.set_languages(2, ('en', 'ru'))
            store.set_languages(1, ('en', 'ru', 'ro'), 20)
            self.assertEqual(store.get_channel_languages(1, 20), ('en', 'ru', 'ro'))
            self.assertEqual(store.get_channel_languages(1, 999), ('en', 'ru'))
            self.assertTrue(store.is_channel_enabled(1, 20))
            self.assertFalse(store.is_channel_enabled(2, 21))
            self.assertEqual(store.get_user_mode(1, 40), 'off')
            store.close()

    def test_multitarget_translation_uses_full_context_each_time(self):
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(side_effect=['Это полное предложение.', 'Aceasta este o propoziție completă.'])
        result = translator.translate_languages('This is a complete sentence.', 'auto', ('en', 'ru', 'ro'))
        self.assertEqual(result.target, 'ru,ro')
        self.assertIn('**Russian**', result.text)
        self.assertIn('**Romanian**', result.text)
        self.assertEqual(translator._request.call_count, 2)
        for call in translator._request.call_args_list:
            self.assertIn('This is a complete sentence.', call.args[0])

    def test_full_source_echo_is_removed_before_output_validation(self):
        translator = LocalTranslator('http://localhost:11434')
        source = 'Please keep the original message and translate the entire sentence.'
        translator._request = Mock(return_value=source + '\n\nTe rog, păstrează mesajul original și traduce toată propoziția.')
        result = translator.translate_to(source, 'ro', 'en')
        self.assertNotIn(source, result.text)
        self.assertTrue(result.text.startswith('Te rog'))

    def test_wrong_language_is_rejected_even_when_both_use_latin_script(self):
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(return_value='This is an English answer, not a French translation of the original message.')
        with self.assertRaises(TranslationUnavailable):
            translator.translate_to('Can you check the translation?', 'fr', 'en')

    def test_temporary_expiry_and_sources_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.db'
            store = SettingsStore(path)
            store.add_temporary(80, 1, 20, 70, 'en', 'ro', time.time() - 1, [30, 31])
            store.close()
            store = SettingsStore(path)
            self.assertEqual(store.temporary_sources(80), [30, 31])
            self.assertEqual(len(store.temporary_posts(expires_before=time.time())), 1)
            self.assertEqual(len(store.temporary_posts(source_ids={31})), 1)
            store.remove_temporary(80)
            self.assertEqual(store.temporary_posts(), [])
            self.assertEqual(store.temporary_sources(80), [])
            store.close()

    def test_feedback_language_migration_preserves_previous_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.db'
            store = SettingsStore(path)
            values = dict(guild_id=1, channel_id=20, source_message_id=30, translation_message_id=80,
                          source_author_id=40, reporter_id=50, source_language='en', target_language='ru',
                          source_text='Hello', translated_text='Привет', corrected_text=None, note='Check tone')
            store.add_feedback(**values)
            store.close()
            with sqlite3.connect(path) as db:
                schema = db.execute("SELECT sql FROM sqlite_master WHERE name='translation_feedback'").fetchone()[0]
                schema = schema.replace('source_language TEXT NOT NULL', "source_language TEXT NOT NULL CHECK(source_language IN ('en', 'ru'))")
                schema = schema.replace('target_language TEXT NOT NULL', "target_language TEXT NOT NULL CHECK(target_language IN ('en', 'ru'))")
                db.execute('ALTER TABLE translation_feedback RENAME TO old_feedback')
                db.execute(schema)
                db.execute('INSERT INTO translation_feedback SELECT * FROM old_feedback')
                db.execute('DROP TABLE old_feedback')
            db.close()
            store = SettingsStore(path)
            self.assertEqual(store.open_feedback_count(1), 1)
            row = store._connection.execute('SELECT * FROM translation_feedback').fetchone()
            self.assertEqual(row['note'], 'Check tone')
            values.update(translation_message_id=81, target_language='ro', translated_text='Salut')
            self.assertTrue(store.add_feedback(**values))
            self.assertEqual(store.open_feedback_count(1), 2)
            store.close()
