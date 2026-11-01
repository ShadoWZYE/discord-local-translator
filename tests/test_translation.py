from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock

from translator_bot.bot import split_for_discord
from translator_bot.translation import LocalTranslator, TranslationUnavailable, detect_language, direction_for, MUSIC_COMMANDS, is_bot_command
from translator_bot.storage import SettingsStore


class DetectionTests(unittest.TestCase):
    def test_music_and_slash_commands_never_reach_model(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(side_effect=AssertionError('Command must not reach model'))
        commands = [f'-{command} music query' for command in MUSIC_COMMANDS]
        commands += ['/play music query', ' /24/7', '-FP Song title', '-p русская песня']
        for text in commands:
            with self.subTest(text=text):
                self.assertTrue(is_bot_command(text))
                for mode in ('auto', 'en_to_ru', 'ru_to_en'):
                    self.assertIsNone(translator.translate(text, mode))
                    self.assertIsNone(translator.translate_languages(text, mode, ('en', 'ru', 'es')))
                self.assertIsNone(translator.translate_to(text, 'ru', 'en'))
        translator._request.assert_not_called()

    def test_command_discussion_and_normal_bullets_are_not_commands(self) -> None:
        for text in ('Use -p to play music', '- Please play some music',
                     '-playful people', '-pauseworthy moment', '/ a normal slash',
                     'I like this song\n-play another song'):
            self.assertFalse(is_bot_command(text), text)
            self.assertIsNotNone(direction_for(text, 'auto'), text)

    def test_detects_english(self) -> None:
        self.assertEqual(detect_language("Hello, how are you?"), "en")

    def test_detects_russian(self) -> None:
        self.assertEqual(detect_language("Привет, как дела?"), "ru")

    def test_ignores_discord_and_web_syntax(self) -> None:
        self.assertEqual(detect_language("<@123> https://example.com"), None)

    def test_direction_modes(self) -> None:
        self.assertEqual(direction_for("Привет", "auto"), ("ru", "en"))
        self.assertEqual(direction_for("Привет", "en_to_ru"), ("en", "ru"))
        self.assertIsNone(direction_for("Hello", "off"))

    def test_no_text_never_reaches_model_even_in_forced_modes(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(side_effect=AssertionError('Model must not be called'))
        for text in ('', ' \n ', '😀🎉❤️', '12345!?', '<:party:123456>',
                     '<a:dance:123456>', '<@123> <#456> <@&789>',
                     '@everyone', '@here', '@everyone @here 😀',
                     'https://example.com/image.gif', 'www.example.com',
                     '`hello`', '```python\nprint("hello")\n```',
                     '<t:1234567890:R>', '</translate-help:123456>'):
            for mode in ('auto', 'en_to_ru', 'ru_to_en'):
                with self.subTest(text=text, mode=mode):
                    self.assertIsNone(translator.translate(text, mode))
        translator._request.assert_not_called()

    def test_captions_and_text_with_emoji_still_translate(self) -> None:
        self.assertEqual(direction_for('Look at this 😀 https://example.com/image.png', 'auto'), ('en', 'ru'))
        self.assertEqual(direction_for('Посмотри 😀', 'auto'), ('ru', 'en'))


class TranslationQualityTests(unittest.TestCase):
    def test_outer_brackets_are_restored_outside_model(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(return_value='Пусть она дочитает.')
        self.assertEqual(translator.translate_to('[Let her finish reading.]', 'ru', 'en').text,
                         '[Пусть она дочитает.]')
        self.assertNotIn('[Let her', translator._request.call_args.args[0])

    def test_outer_brackets_keep_tildes_and_emphasis(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(return_value=json.dumps({
            'segment_0': 'Подожди', 'segment_2': 'я сказал', 'segment_5': 'только', 'segment_8': 'это.'}))
        self.assertEqual(translator.translate_to('[Wait ~ I said **only** this.]', 'ru', 'en').text,
                         '[Подожди ~ я сказал **только** это.]')

    def test_cyrillic_rank_identifier_is_normalized_and_protected(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(return_value='He is now [[DCTOKEN_0]].')
        self.assertEqual(translator.translate_to('Он теперь р5.', 'en', 'ru').text, 'He is now R5.')
        translator._request.assert_called_once()

    def test_rank_normalization_does_not_change_code_or_urls(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(return_value='He is now [[DCTOKEN_0]]. Code [[DCTOKEN_1]] and [[DCTOKEN_2]].')
        self.assertEqual(translator.translate_to('Он теперь р5. Код `r5` и https://example.com/r5', 'en', 'ru').text,
            'He is now R5. Code `r5` and https://example.com/r5.')

    def test_english_translation_cannot_leave_a_russian_phrase(self) -> None:
        with self.assertRaises(TranslationUnavailable):
            LocalTranslator._validate_output('Мы должны выступить против них.',
                'We should выступить против them.', 'en', 'ru')
        LocalTranslator._validate_output('Сохрани выражение «русский язык» буквально.',
            'Keep the expression «русский язык» literally.', 'en', 'ru')

    def test_fixed_segments_preserve_markers_on_first_call(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        def response(prompt, **kwargs):
            if not kwargs['format_schema']['required'][0].startswith('segment_'):
                return json.dumps({'part_0': 'Проверь', 'part_1': 'только это.'})
            words = iter(('Проверь', 'только', 'это.'))
            return json.dumps({key: prop['const'] if 'const' in prop else next(words)
                for key, prop in kwargs['format_schema']['properties'].items()})
        translator._request = Mock(side_effect=response)
        result = translator.translate_to('Check <@123> ~ **only** this.', 'ru', 'en')
        self.assertEqual(result.text, 'Проверь <@123> ~ **только** это.')
        self.assertEqual(translator._request.call_count, 1)

    def test_plain_bold_uses_fixed_segments_without_wasted_plain_call(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        def response(prompt, **kwargs):
            if not kwargs:
                return 'Это только тест.'
            words = iter(('Это', 'только', 'тест.'))
            return json.dumps({key: prop['const'] if 'const' in prop else next(words)
                for key, prop in kwargs['format_schema']['properties'].items()})
        translator._request = Mock(side_effect=response)
        result = translator.translate_to('This is **only** a test.', 'ru', 'en')
        self.assertEqual(result.text, 'Это **только** тест.')
        self.assertEqual(translator._request.call_count, 1)

    def test_broadcast_mentions_with_real_text_are_protected(self) -> None:
        translator=LocalTranslator('http://localhost:11434')
        translator._request=Mock(return_value='Привет [[DCTOKEN_0]], проверьте [[DCTOKEN_1]].')
        result=translator.translate_to('Hello @everyone, check @here.','ru','en')
        self.assertEqual(result.text,'Привет @everyone, проверьте @here.')

    def test_russian_laughter_only_is_rendered_without_model_inference(self) -> None:
        translator=LocalTranslator('http://localhost:11434')
        translator._request=Mock(side_effect=AssertionError('Laughter needs no neural inference'))
        for source, expected in (
            ('Ахахаахахаххахахаха', 'Ahahaahahahhahahaha'),
            ('ХАХАХА!', 'HAHAHA!'),
            ('Хаха\nахах...', 'Haha\nahah...'),
        ):
            self.assertEqual(translator.translate_to(source,'en','ru').text,expected)
        translator._request.assert_not_called()

    def test_laughter_handler_does_not_take_sighs_or_regular_sentences(self) -> None:
        for source, expected in (('Ах.', 'Ah.'), ('Хаос.', 'Chaos.'),
                                 ('Хаха, я рад.', 'Haha, I am glad.')):
            translator=LocalTranslator('http://localhost:11434')
            translator._request=Mock(return_value=expected)
            self.assertEqual(translator.translate_to(source,'en','ru').text,expected)
            translator._request.assert_called_once()

    def test_laughter_line_cannot_repeat_adjacent_dialogue(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(return_value=json.dumps(
            {'part_1': 'I am going home.'}))
        self.assertEqual(translator.translate_to('Хаха\nЯ иду домой.', 'en', 'ru').text,
                         'Haha\nI am going home.')
        translator._request.assert_called_once()
        schema = translator._request.call_args.kwargs['format_schema']
        self.assertNotIn('part_0', schema['properties'])
        translator.clear_cache()
        translator._request = Mock(return_value=json.dumps(
            {'part_0': 'Haha, I am going home.', 'part_1': 'I am going home.'}))
        with self.assertRaises(TranslationUnavailable):
            translator.translate_to('Хаха\nЯ иду домой.', 'en', 'ru')

    def test_causal_thanks_to_is_not_direct_gratitude(self) -> None:
        prompt = LocalTranslator('http://localhost:11434')._prompt(
            'I am happy thanks to the weather.', 'en', 'ru')
        self.assertIn('Causal phrase', prompt)
        self.assertIn('благодаря', prompt)

    def test_polysemous_noun_hint_keeps_non_anatomical_meaning(self) -> None:
        prompt = LocalTranslator('http://localhost:11434')._prompt(
            'Он член нашего клуба.', 'ru', 'en')
        self.assertIn('member in a group or organization', prompt)
        self.assertIn('Choose from the actual context', prompt)

    def test_titles_are_not_invented_names_or_vocatives(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        self.assertIn("invented person's name", translator._prompt(
            'Доброй ночи, Дворецкий.', 'ru', 'en'))
        self.assertIn('sentence subject', translator._prompt(
            'Мисс «Загадка» что-нибудь предприняла?', 'ru', 'en'))

    def test_reported_possession_is_not_past_tense_eating(self) -> None:
        prompt = LocalTranslator('http://localhost:11434')._prompt(
            'Она сказала, что есть.', 'ru', 'en')
        self.assertIn('reported existence or possession', prompt)
        self.assertIn('Do not invent I had eaten', prompt)

    def test_chat_idioms_and_typo_hints_are_contextual(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        self.assertIn('What do you mean?', translator._prompt('В плане?', 'ru', 'en'))
        self.assertIn('I swear', translator._prompt('Отвечаю.', 'ru', 'en'))
        self.assertIn('actual borrowing/lending', translator._prompt(
            'They knew nothing about the borrows.', 'en', 'ru'))

    def test_pronoun_guidance_preserves_roles_and_impersonal_ownership(self) -> None:
        translator=LocalTranslator('http://localhost:11434')
        prompt=translator._prompt('Хочешь сама тебе уши и хвост сделаю?','ru','en')
        self.assertIn('I will make it myself',prompt)
        self.assertIn('recipient for you',prompt)
        self.assertIn('Resolve omitted subjects',prompt)
        prompt=translator._prompt('Но я думаю, стоит поменять аватарку.','ru','en')
        self.assertIn('Do not invent you should',prompt)
        self.assertIn('unspecified ownership unspecified',prompt)

    def test_translated_apologies_and_inability_statements_are_not_refusals(self) -> None:
        for source, output in (
            ('Прости, у меня был дурдом. Да и продолжается.',
             "Sorry, things have been chaotic here. And still are."),
            ('Я не могу помочь тебе сегодня.', 'I cannot help you today.'),
            ('Я не могу прийти сегодня.', "I can't come today."),
        ):
            with self.subTest(source=source):
                translator = LocalTranslator('http://localhost:11434')
                translator._request = Mock(return_value=output)
                self.assertEqual(translator.translate_to(source,'en','ru').text,output)
                translator._request.assert_called_once()

    def test_explicit_translation_wrapper_is_still_rejected(self) -> None:
        with self.assertRaises(TranslationUnavailable):
            LocalTranslator._validate_output('Привет.', 'Translation: Hello.', 'en', 'ru')

    def test_missing_mentions_retry_full_source_with_fixed_syntax(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        source = 'Please ask <@123> and <@456> to check the server.'
        def request(prompt, **kwargs):
            if 'format_schema' not in kwargs:
                return 'Пожалуйста, попроси их проверить сервер.'
            prose = iter(['Попроси', 'и', 'проверить сервер.'])
            return json.dumps({key: value['const'] if 'const' in value else next(prose)
                for key, value in kwargs['format_schema']['properties'].items()})
        translator._request = Mock(side_effect=request)
        with self.assertLogs('translator_bot', level='WARNING'):
            result = translator.translate(source, 'en_to_ru')
        self.assertIn('<@123>', result.text)
        self.assertIn('<@456>', result.text)
        self.assertEqual(translator._request.call_count, 2)
        self.assertIn('check the server', translator._request.call_args.args[0])
        self.assertIn('format_schema', translator._request.call_args.kwargs)

    def test_successful_placeholder_translation_does_not_retry(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(return_value='Пожалуйста, попроси [[DCTOKEN_0]] проверить перевод.')
        result = translator.translate('Please ask <@123> to check the translation.', 'en_to_ru')
        self.assertIn('<@123>', result.text)
        translator._request.assert_called_once()

    def test_placeholder_case_drift_preserves_exact_mention_without_retry(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(return_value='Пожалуйста, попроси [[DCToken_0]] проверить перевод.')
        result = translator.translate('Please ask <@123> to check the translation.', 'en_to_ru')
        self.assertEqual(result.text, 'Пожалуйста, попроси <@123> проверить перевод.')
        translator._request.assert_called_once()

    def test_structured_placeholder_case_drift_keeps_tilde_and_field(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(return_value=json.dumps(
            {'part_0': '[[dctoken_0]] Пожалуйста', 'part_1': 'помоги собрать очередь.'}))
        result = translator.translate_to('<@123> Please~ help build the queue.', 'ru', 'en')
        self.assertEqual(result.text, '<@123> Пожалуйста~ помоги собрать очередь.')
        translator._request.assert_called_once()

    def test_case_normalization_still_rejects_wrong_or_reordered_tokens(self) -> None:
        for value in ('[[dctoken_9]]', '[[dctoken_0]][[dctoken_0]]',
                      '[[dctoken_1]][[dctoken_0]]', '[[dctoken_00]][[dctoken_1]]'):
            with self.subTest(value=value), self.assertRaises(TranslationUnavailable):
                LocalTranslator._restore(value, {'[[DCTOKEN_0]]': '<@123>', '[[DCTOKEN_1]]': '<@456>'})

    def test_structured_json_quote_failure_retries_full_context_once(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(side_effect=[
            '{"part_0":"Это "слово"","part_1":"требует пояснения."}',
            json.dumps({'part_0': 'Это «слово»', 'part_1': 'требует пояснения.'})])
        with self.assertLogs('translator_bot', level='WARNING'):
            result = translator.translate_to('This word ~ needs explanation.', 'ru', 'en')
        self.assertEqual(result.text, 'Это «слово» ~ требует пояснения.')
        self.assertEqual(translator._request.call_count, 2)
        for call in translator._request.call_args_list:
            self.assertIn('This word', call.args[0])
            self.assertIn('needs explanation', call.args[0])
            self.assertNotIn('pattern', call.kwargs['format_schema']['properties']['part_0'])

    def test_structured_retry_has_a_two_call_limit(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(return_value='not valid JSON')
        with self.assertLogs('translator_bot', level='WARNING'), self.assertRaises(TranslationUnavailable):
            translator.translate_to('This word ~ needs explanation.', 'ru', 'en')
        self.assertEqual(translator._request.call_count, 2)

    def test_multiline_structured_mismatch_retries_within_budget(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(side_effect=[
            'Listen\n\nI will send you screenshots.',
            json.dumps({'part_0': 'Listen', 'part_1': 'I will send you screenshots.'})])
        with self.assertLogs('translator_bot', level='WARNING'):
            result = translator.translate_to('Послушай\nЯ пришлю тебе скриншоты.', 'en', 'ru')
        self.assertEqual(result.text, 'Listen\nI will send you screenshots.')
        self.assertEqual(translator._request.call_count, 2)
        self.assertIn('format_schema', translator._request.call_args_list[0].kwargs)
        self.assertIn('format_schema', translator._request.call_args_list[1].kwargs)

    def test_plain_layout_fallback_cannot_make_a_third_request(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(side_effect=['Listen\n\nI will send you screenshots.', 'bad JSON'])
        with self.assertLogs('translator_bot', level='WARNING'), self.assertRaises(TranslationUnavailable):
            translator.translate_to('Послушай\nЯ пришлю тебе скриншоты.', 'en', 'ru')
        self.assertEqual(translator._request.call_count, 2)

    def test_long_neighbor_cannot_hide_a_truncated_fragment(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(return_value=json.dumps({
            'part_0': 'Да.', 'part_1': 'Соседний длинный абзац не должен скрывать пропущенный вопрос.'}))
        with self.assertLogs('translator_bot', level='WARNING'), self.assertRaises(TranslationUnavailable):
            translator.translate_to('Do you think I misunderstood your explanation? ~ '
                'This neighboring paragraph should not conceal the missing question.', 'ru', 'en')
        self.assertEqual(translator._request.call_count, 2)

    def test_mixed_viewpoint_and_implicit_object_guidance(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        prompt = translator._prompt('*Она повернулась к нему. Я положила руку ему на плечо.*', 'ru', 'en')
        self.assertIn('Keep deliberate shifts between first-person and third-person', prompt)
        self.assertIn('do not swap participants', prompt)
        self.assertIn('Never insert bracketed guesses', prompt)
        translator._request = Mock(return_value=json.dumps({'segment_1':'She turned toward him. I put my hand on his shoulder.'}))
        self.assertEqual(translator.translate_to(
            '*Она повернулась к нему. Я положила руку ему на плечо.*', 'en', 'ru').text,
            '*She turned toward him. I put my hand on his shoulder.*')
        translator._request.assert_called_once()

    def test_retry_rejects_missing_changed_duplicated_or_reordered_syntax(self) -> None:
        for retry in ('Попроси <@123> проверить перевод.',
                      'Попроси <@123> и <@999> проверить перевод.',
                      'Попроси <@123>, <@123> и <@456> проверить перевод.',
                      'Попроси <@456> и <@123> проверить перевод.'):
            with self.subTest(retry=retry):
                translator = LocalTranslator('http://localhost:11434')
                translator._request = Mock(side_effect=['Попроси их проверить перевод.', retry])
                with self.assertLogs('translator_bot', level='WARNING'), self.assertRaises(TranslationUnavailable):
                    translator.translate('Ask <@123> and <@456> to check the translation.', 'en_to_ru')
                self.assertEqual(translator._request.call_count, 2)

    def test_retry_preserves_code_urls_and_custom_emoji_as_well(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        source = 'Ask <@123> to check `print(1)` at https://example.com <:happy:456>'
        def request(prompt, **kwargs):
            if 'format_schema' not in kwargs:
                return 'Попроси проверить код.'
            return json.dumps({key: value['const'] if 'const' in value else 'Проверь'
                for key, value in kwargs['format_schema']['properties'].items()})
        translator._request = Mock(side_effect=request)
        with self.assertLogs('translator_bot', level='WARNING'):
            result = translator.translate(source, 'en_to_ru')
        self.assertIn('`print(1)`', result.text)
        self.assertIn('https://example.com', result.text)
        self.assertIn('<:happy:456>', result.text)

    def test_chat_reference_markers_are_protected_and_restored(self) -> None:
        source = 'This^ works. ~Now see <#123> and ~~strikethrough~~.'
        protected, markers = LocalTranslator._protect(source)
        self.assertEqual(list(markers.values()), ['~', '<#123>', '~~', '~~'])
        self.assertIn('strikethrough', protected)
        self.assertEqual(LocalTranslator._restore(protected, markers), source)

    def test_missing_reference_marker_is_rejected(self) -> None:
        _, markers = LocalTranslator._protect('Look <#123>.')
        with self.assertRaises(TranslationUnavailable):
            LocalTranslator._restore('Это работает.', markers)

    def test_prompt_addresses_feedback_without_changing_inference_settings(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        prompt = translator._prompt('Please tell me what you think.', 'en', 'ru')
        self.assertIn('comparable intensity', prompt)
        self.assertIn('casual requests into orders', prompt)
        self.assertIn('rhetorical questions', prompt)
        self.assertIn('Never censor, sanitize, soften', prompt)

    def test_single_message_prompt_never_injects_unrelated_examples(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        ordinary = translator._prompt('Hello, how are you?', 'en', 'ru')
        relevant = translator._prompt('Better than whatever the fuck it was doing.', 'en', 'ru')
        russian = translator._prompt('Привет!', 'ru', 'en')
        self.assertEqual(len(translator._messages(ordinary)), 1)
        self.assertEqual(len(translator._messages(relevant)), 1)
        self.assertEqual(len(translator._messages(russian)), 1)
        self.assertEqual(translator._messages(relevant)[-1]['content'], relevant)
        self.assertNotIn('[[DCTOKEN_', ordinary)
        protected = translator._prompt('Check [[DCTOKEN_0]].', 'en', 'ru')
        self.assertIn('Copy these protected tokens exactly once', protected)

    def test_uncensored_phrase_guidance_covers_both_directions(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        for text, source, target, expected in [
            ('This fucking bot works.', 'en', 'ru', 'ебучий'),
            ('Блядь, опять сломалось.', 'ru', 'en', 'fuck'),
        ]:
            with self.subTest(source=source):
                messages = translator._messages(translator._prompt(text, source, target))
                self.assertEqual(len(messages), 1)
                self.assertIn(expected, messages[0]['content'])

    def test_russian_idiom_hints_preserve_meaning_and_intensity(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        prompt = translator._prompt('На тоненького раскатали педиков блядских, пиздато отыграли. Не без проебов, их на парашу, а может и на хуй.', 'ru', 'en')
        for meaning in ('narrow margin', 'NOT mean pedophiles', 'played fucking brilliantly',
                        'fuck-ups', 'send them to the shitter', 'fuck off'):
            self.assertIn(meaning, prompt)

    def test_sleep_and_sexual_details_are_not_software_or_romance(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        prompt = translator._prompt('Охуеваю со своего сна, в три как штык со стояком, закончим вялым по губам.', 'ru', 'en')
        for meaning in ('never a software setup', 'awake at the stated time', 'with a boner', 'NOT a kiss', 'keep it unstated'):
            self.assertIn(meaning, prompt)
        self.assertNotIn('interpreting software, bot, and game terms', prompt)

    def test_context_hints_are_not_inserted_into_unrelated_messages(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        for source, target, text in [('ru', 'en', 'Привет! Как дела?'), ('en', 'ru', 'Swear words should be translated accurately.')]:
            prompt = translator._prompt(text, source, target)
            self.assertNotIn('Mandatory tone and phrase guidance', prompt)
            self.assertIn('Do not add swearing', prompt)

    def test_profanity_wordplay_and_feeding_formula_hints(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        self.assertIn('vulgar twist', translator._prompt('Хуйни за маму и за папу по ложечке', 'ru', 'en'))
        self.assertIn('my uses of the word fuck', translator._prompt('I like my fucks to stay.', 'en', 'ru'))
        self.assertIn('ложечку за маму', translator._prompt('Down the hatch, one spoonful for Mom, one for Dad.', 'en', 'ru'))

    def test_wording_never_triggers_profanity_rejection_or_retry(self) -> None:
        for text, output, source, target in [
            ('Блядь, опять сломалось.', 'Damn, it broke again.', 'ru', 'en'),
            ('Swear words are being translated.', 'Черт возьми, ругательства переводятся.', 'en', 'ru'),
            ('My shitty Japanese.', 'Мой отвратительный японский.', 'en', 'ru'),
            ('Fucking great, no bullshit.', 'Охуенно, никакой чуши.', 'en', 'ru'),
            ('Педики блядские проиграли.', 'Those fucking pedos lost.', 'ru', 'en'),
        ]:
            with self.subTest(text=text):
                translator = LocalTranslator('http://localhost:11434')
                translator._request = Mock(return_value=output)
                result = translator.translate_to(text, target, source)
                self.assertEqual(result.text, output)
                translator._request.assert_called_once()

    def test_syntax_retry_does_not_judge_returned_profanity(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        def request(prompt, **kwargs):
            if 'format_schema' not in kwargs:
                return 'Damn, ask them.'
            prose = iter(['Damn, ask', '.'])
            return json.dumps({key: value['const'] if 'const' in value else next(prose)
                for key, value in kwargs['format_schema']['properties'].items()})
        translator._request = Mock(side_effect=request)
        result = translator.translate_to('Блядь, спроси <@123>.', 'en', 'ru')
        self.assertEqual(result.text, 'Damn, ask <@123>.')
        self.assertEqual(translator._request.call_count, 2)

    def test_tildes_are_protected_including_strikethrough_delimiters(self) -> None:
        text = 'Hello ~ world. ~~Not today~~ ~\nBye ~'
        protected, markers = LocalTranslator._protect(text)
        self.assertEqual(list(markers.values()), ['~', '~~', '~~', '~', '~'])
        self.assertEqual(LocalTranslator._restore(protected, markers), text)

    def test_inline_tilde_boundaries_are_restored_after_full_context_translation(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(side_effect=[
            json.dumps({'part_0':'Возможно, ошибка.', 'part_1':'Ну что ж.', 'part_2':'Продолжим.'}),
        ])
        result = translator.translate_to('Perhaps a bug. ~ Oh well.\nLet us continue.', 'ru', 'en')
        self.assertEqual(result.text, 'Возможно, ошибка. ~ Ну что ж.\nПродолжим.')
        self.assertEqual(translator._request.call_count, 1)
        self.assertIn('Perhaps a bug.', translator._request.call_args.args[0])
        self.assertIn('format_schema', translator._request.call_args.kwargs)

    def test_missing_tildes_are_not_silently_published(self) -> None:
        translator = LocalTranslator('http://localhost:11434')
        translator._request = Mock(return_value='Привет. До свидания.')
        with self.assertRaises(TranslationUnavailable):
            translator.translate_to('Hello. ~ Goodbye.', 'ru', 'en')
        self.assertEqual(translator._request.call_count, 2)


class DiscordChunkTests(unittest.TestCase):
    def test_short_text_is_one_chunk(self) -> None:
        self.assertEqual(split_for_discord("hello"), ["hello"])

    def test_long_text_respects_limit(self) -> None:
        chunks = split_for_discord("word " * 1_000, limit=100)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 100 for chunk in chunks))


class FeedbackStoreTests(unittest.TestCase):
    def test_feedback_is_stored_once_per_reporter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "test.sqlite3")
            values = dict(
                guild_id=1,
                channel_id=2,
                source_message_id=3,
                translation_message_id=4,
                source_author_id=5,
                reporter_id=6,
                source_language="en",
                target_language="ru",
                source_text="hello",
                translated_text="привет",
                corrected_text="здравствуйте",
                note="Tone was too casual",
            )
            self.assertTrue(store.add_feedback(**values))
            self.assertFalse(store.add_feedback(**values))
            self.assertEqual(store.open_feedback_count(1), 1)
            self.assertTrue(store.remove_feedback(4, 6))
            self.assertEqual(store.open_feedback_count(1), 0)
            store.close()


if __name__ == "__main__":
    unittest.main()
