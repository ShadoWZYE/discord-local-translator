import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from translator_bot.bot import combined_message
from translator_bot.translation import LocalTranslator, TranslationUnavailable


class SourceLayoutTests(unittest.TestCase):
    def test_message_boundaries_do_not_depend_on_punctuation_or_emoji(self):
        for first in ('I hope you have some time ❤️','I hope you have some time','I hope you have some time.'):
            messages = [SimpleNamespace(id=i,content=text,guild=None,channel=None,author=None)
                for i,text in enumerate((first,'Why do you ask? 🤔😂😏'))]
            self.assertEqual(combined_message(messages).content,first+'\nWhy do you ask? 🤔😂😏')

    def test_multiline_is_one_context_request_with_code_assembled_layout(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value=json.dumps({'part_0':'Если вы', 'part_1':'готовы,', 'part_2':'дайте мне знать.'}))
        result=t.translate_to('If you\nare ready,\nlet me know.','ru','en')
        self.assertEqual(result.text,'Если вы\nготовы,\nдайте мне знать.')
        t._request.assert_called_once()
        prompt=t._request.call_args.args[0]
        for source in ('If you','are ready,','let me know.'):
            self.assertIn(source,prompt)
        self.assertEqual(t._request.call_args.kwargs['format_schema']['required'],['part_0','part_1','part_2'])

    def test_python_restores_indentation_blank_lines_and_outer_whitespace(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value=json.dumps({'part_0':'  Привет. ', 'part_3':'Пока.'}))
        result=t.translate_to(' \n  Hello.  \n\n \n\tBye.\t\n  ','ru','en')
        self.assertEqual(result.text,' \n  Привет.  \n\n \n\tПока.\t\n  ')
        t._request.assert_called_once()

    def test_flattened_or_embedded_newline_response_is_rejected(self):
        parts={'part_0':'Hello.','part_1':'Why?'}
        for result in ({'part_0':'Привет. Почему?'},
                       {'part_0':'Привет.\nПочему?','part_1':'Почему?'},
                       {'part_0':'Привет.','part_1':'Почему?','part_2':'Лишнее.'}):
            with self.subTest(result=result),self.assertRaises(TranslationUnavailable):
                LocalTranslator._decode_layout(json.dumps(result),parts,{}, {})
