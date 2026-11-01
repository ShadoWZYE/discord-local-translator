import unittest
import json
from unittest.mock import Mock

from translator_bot.translation import LocalTranslator, direction_for, transcript_payload


class TranscriptDetectionTests(unittest.TestCase):
    def test_latin_speaker_labels_do_not_override_russian_dialogue(self):
        text='• [A]Winterfox: "Gute Nacht"\n• [B] Skyfall: "Привет, я рада тебя видеть. Мы будем ждать тебя завтра."'
        self.assertEqual(direction_for(text,'auto'),('ru','en'))
        self.assertNotIn('Winterfox',transcript_payload(text))
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value=json.dumps({'part_0':'[[DCTOKEN_0]][[DCTOKEN_1]]Good night[[DCTOKEN_2]]',
            'part_1':'[[DCTOKEN_3]][[DCTOKEN_4]]Hello, I am happy to see you. We will wait for you tomorrow.[[DCTOKEN_5]]'}))
        out=t.translate(text).text
        self.assertTrue(out.startswith('• [A]Winterfox: "Good night"'))
        self.assertIn('• [B] Skyfall: "Hello',out)
        self.assertIn('Translate EVERY utterance',t._request.call_args.args[0])

    def test_cyrillic_labels_do_not_override_english_dialogue(self):
        text='• [Я]Александра: "Please come tomorrow"\n• [Ю]Екатерина: "Hello, we will see you at the meeting."'
        self.assertEqual(direction_for(text,'auto'),('en','ru'))

    def test_unchanged_transcript_labels_do_not_dominate_output_language(self):
        source='• VeryLongPlayerDisplayName: "Hello friend"\n• AnotherLongPlayerNickname: "How are you today?"'
        output='• VeryLongPlayerDisplayName: "Привет, друг"\n• AnotherLongPlayerNickname: "Как у тебя дела сегодня?"'
        LocalTranslator._validate_output(source,output,'ru','en')

    def test_ordinary_prose_and_no_text_guards_are_preserved(self):
        text='Note: "Hello"\nThis is my reply.'
        self.assertEqual(transcript_payload(text),text)
        self.assertEqual(transcript_payload('Sam: "Hello"'),'Sam: "Hello"')
        self.assertIsNone(direction_for('• Sam: "😀"\n• Alex: "😅"','auto'))

    def test_targeted_semantic_hints_keep_literal_counterexamples(self):
        t=LocalTranslator('http://localhost:11434')
        hint=t._prompt('Вот же жук!','ru','en')
        self.assertIn('sly rascal',hint)
        self.assertIn('In insect/nature context keep beetle/bug',hint)
        self.assertIn('Track actuality',t._prompt('Желаю теперь, что отказалась.','ru','en'))
        self.assertNotIn('Track actuality',t._prompt('Желаю вам хорошего дня.','ru','en'))
