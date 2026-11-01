import unittest
import json
from unittest.mock import Mock

from translator_bot.name_rendering import localized_name, name_bindings
from translator_bot.translation import LocalTranslator, PROTECTED_PATTERN, direction_for


class NameRenderingTests(unittest.TestCase):
    def test_recognizable_compounds_work_in_both_directions(self):
        for english, russian in [('ChaosGoblin', 'ХаосГоблин'), ('LadyGros', 'ЛедиГрос'),
                                 ('ShadowFox', 'ТеньЛис'), ('AliceKnight', 'АлисаРыцарь')]:
            self.assertEqual(localized_name(english, 'en', 'ru'), russian)
            self.assertEqual(localized_name(russian, 'ru', 'en'), english)

    def test_conventional_personal_spellings_not_cultural_substitutions(self):
        self.assertEqual(localized_name('John', 'en', 'ru'), 'Джон')
        self.assertEqual(localized_name('Иван', 'ru', 'en'), 'Ivan')
        self.assertIsNone(localized_name('Mark', 'en', 'ru'))
        self.assertIsNone(localized_name('Gross', 'en', 'ru'))
        self.assertIsNone(localized_name('Shadow', 'en', 'ru'))
        self.assertIsNone(localized_name('XyzzFox', 'en', 'ru'))

    def test_code_links_mentions_tagged_and_numbered_handles_are_unchanged(self):
        source = '`John` https://example.com/John <@123> @John John_7 John-7 John#1234 John.test R4'
        self.assertEqual(name_bindings(source, 'en', 'ru', PROTECTED_PATTERN), {})
        self.assertEqual(name_bindings('`ChaosGoblin` https://example.com/ChaosGoblin <a:ChaosGoblin:123>',
                                      'en', 'ru', PROTECTED_PATTERN), {})
        self.assertEqual(name_bindings('John spoke.', 'en', 'ru', PROTECTED_PATTERN), {'John': 'Джон'})

    def test_english_personal_names_keep_grammar_compound_handles_are_rendered_locally(self):
        t = LocalTranslator('http://localhost:11434')
        t._request = Mock(return_value='Джон встретил [[DCTOKEN_0]].')
        out = t.translate_to('John met ChaosGoblin.', 'ru', 'en').text
        self.assertEqual(out, 'Джон встретил ХаосГоблин.')
        t._request.assert_called_once()
        self.assertIn('John → Джон', t._request.call_args.args[0])
        t._request = Mock(return_value='Я поговорил с Джоном.')
        self.assertEqual(t.translate_to('I spoke to John.', 'ru', 'en').text, 'Я поговорил с Джоном.')

    def test_name_rendering_works_in_structured_paragraphs(self):
        t = LocalTranslator('http://localhost:11434')
        t._request = Mock(return_value=json.dumps({'part_0':'[[DCTOKEN_0]] is here.',
                                                  'part_2':'Hello.'}))
        self.assertEqual(t.translate_to('ХаосГоблин здесь.\n\nПривет.', 'en', 'ru').text,
                         'ChaosGoblin is here.\n\nHello.')
        t._request.assert_called_once()

    def test_russian_names_in_sentences_render_in_english(self):
        t = LocalTranslator('http://localhost:11434')
        t._request = Mock(return_value='[[DCTOKEN_0]] and [[DCTOKEN_1]] have joined.')
        self.assertEqual(t.translate_to('ЛедиГрос и ХаосГоблин присоединились.', 'en', 'ru').text,
                         'LadyGros and ChaosGoblin have joined.')

    def test_standalone_recognized_names_need_no_model_call(self):
        t = LocalTranslator('http://localhost:11434')
        t._request = Mock(side_effect=AssertionError('No inference for known standalone names'))
        self.assertEqual(direction_for('ChaosGoblin', 'auto'), ('en', 'ru'))
        self.assertEqual(direction_for('ХаосГоблин', 'auto'), ('ru', 'en'))
        self.assertEqual(t.translate('ChaosGoblin').text, 'ХаосГоблин')
        self.assertEqual(t.translate('Ангелина').text, 'Angelina')
        self.assertEqual(t.translate_to('[John]', 'ru', 'en').text, '[Джон]')
        t._request.assert_not_called()

    def test_ambiguous_compound_handles_remain_verbatim(self):
        t = LocalTranslator('http://localhost:11434')
        t._request = Mock(return_value='[[DCTOKEN_0]] присоединился.')
        self.assertEqual(t.translate_to('ZephyrQuux joined.', 'ru', 'en').text,
                         'ZephyrQuux присоединился.')

    def test_name_rendering_preserves_emoji_and_newlines(self):
        t = LocalTranslator('http://localhost:11434')
        t._request = Mock(return_value=json.dumps({'part_0':'Алиса здесь.','part_1':'Привет [[DCTOKEN_0]]'}))
        self.assertEqual(t.translate_to('Alice is here.\nHello 🙂', 'ru', 'en').text,
                         'Алиса здесь.\nПривет 🙂')
        t._request.assert_called_once()
