"""Synthetic regressions for classes of reported failures; no private transcripts."""
import json
import unittest
from unittest.mock import Mock

from translator_bot.translation import LocalTranslator, TranslationUnavailable, direction_for, detect_language


class ReportRegressions(unittest.TestCase):
    def test_mention_only_lines_are_fixed_not_rewritten_into_duplicate_prose(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value=json.dumps({'part_0':'Обновление готово. Что думаете?'}))
        self.assertEqual(t.translate_to('The update is ready. Thoughts?\n@here','ru','en').text,
            'Обновление готово. Что думаете?\n@here')
        self.assertEqual(t._request.call_count,1)
        self.assertNotIn('part_1',t._request.call_args.kwargs['format_schema']['properties'])
    def test_trust_reassurance_hint_does_not_change_ordinary_advice(self):
        t=LocalTranslator('http://localhost:11434')
        self.assertIn('can I trust',t._prompt('Стоит довериться твоему опыту?', 'ru','en'))
        self.assertNotIn('can I trust',t._prompt('Стоит сходить к врачу?', 'ru','en'))
        self.assertNotIn('can I trust',t._prompt('Стоит доверять незнакомцам?', 'ru','en'))
    def test_third_person_dismissals_keep_the_person_and_retry_within_budget(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(side_effect=AssertionError('Fixed dismissals need no model'))
        self.assertEqual(t.translate_to('Пошёл он нахер.', 'en', 'ru').text, 'Fuck him.')
        self.assertEqual(t.translate_to('Пошла она нахуй!', 'en', 'ru').text, 'Fuck her!')
        self.assertEqual(t._request.call_count,0)
        with self.assertRaises(TranslationUnavailable):
            t._validate_participant_roles('Пошёл он нахер.', 'Go fuck him.', 'ru','en')
        for source,output in [('Пошли они нахуй.', 'Fuck them.'),
                              ('Идите нахуй.', 'Go fuck yourselves.'),
                              ('Пошли мы в парк.', 'We went to the park.')]:
            t._validate_participant_roles(source,output,'ru','en')

    def test_idiom_binding_preserves_the_address_and_emoji(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value='[[DCTOKEN_0]], buddy! [[DCTOKEN_1]]')
        self.assertEqual(t.translate_to('С лёгким паром, дружище! 🙂','en','ru').text,
            'Hope you enjoyed your bath, buddy! 🙂')
        self.assertEqual(t._request.call_count,1)
        text,tokens=t._protect('Мясо приготовлено на пару.','ru','en')
        self.assertFalse(tokens)
        text,tokens=t._protect('R4 HTTPS://example.com «Привет»','ru','en',preserve_quotes=True)
        self.assertEqual(list(tokens.values()),['R4','HTTPS://example.com','«','»'])

    def test_repetition_is_not_replaced_with_a_euphemism(self):
        t=LocalTranslator('http://localhost:11434')
        with self.assertRaises(TranslationUnavailable):
            t._validate_participant_roles('Иначе захочется еще раз… кхм…', 'Otherwise, I might want to... well, you know.', 'ru','en')
        for output in ['Otherwise, I might want to do it again... ahem...',
                       'Otherwise, I might want another go... ahem...']:
            t._validate_participant_roles('Иначе захочется ещё раз… кхм…',output,'ru','en')

    def test_quote_scope_is_fixed_across_inline_separators(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value=json.dumps({'part_0':'[[DCTOKEN_0]]Привет.',
            'part_1':'Это опасение.[[DCTOKEN_1]]','part_2':'Вот почему.'}))
        self.assertEqual(t.translate_to('"Hello. ~ This is a concern." ~ Here is why.', 'ru','en').text,
            '"Привет. ~ Это опасение." ~ Вот почему.')
        self.assertEqual(t._request.call_count,1)
        protected, tokens=t._protect('«Hello»',preserve_quotes=True)
        with self.assertRaises(TranslationUnavailable):
            t._decode_layout(json.dumps({'part_0':'"[[DCTOKEN_0]]Привет[[DCTOKEN_1]]"'}),
                {'part_0':protected},{},tokens)

    def test_elongated_affectionate_interjections_are_local_but_not_prose(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(side_effect=AssertionError('Interjection needs no model'))
        self.assertEqual(t.translate_to('Aawwww…🫠','ru','en').text,'Ооооо…🫠')
        self.assertIsNone(t._fixed_chat('Aww, poor thing.','en','ru'))
        self.assertIsNone(t._fixed_chat('Aww https://example.com','en','ru'))
        self.assertIsNone(t._fixed_chat('Awful weather','en','ru'))

    def test_semantic_guidance_covers_classes_without_growing_common_prompts(self):
        t=LocalTranslator('http://localhost:11434')
        self.assertIn('common nouns',t._prompt('Лев рычит в клетке.','ru','en'))
        self.assertIn('named child',t._prompt('Мы гуляем с Львом.','ru','en'))
        self.assertIn('during the day is not all day',t._prompt('Я могу в течение дня.','ru','en'))
        self.assertIn('challenge',t._prompt('А то что?','ru','en'))
        self.assertLess(len(t._prompt('Hello there.','en','ru')),2500)

    def test_speech_act_and_homonym_checks_have_negative_controls(self):
        t=LocalTranslator('http://localhost:11434')
        for source,wrong in [('А то что?', 'And so what?'),
                             ('Льву принесли мясо в клетку.', 'They brought Leo meat in his cage.'),
                             ('С легким паром!', 'With light steam!')]:
            with self.subTest(source=source), self.assertRaises(TranslationUnavailable):
                t._validate_participant_roles(source,wrong,'ru','en')
        for source,good in [('И что?', 'So what?'),
                            ('Я гуляю с Львом.', 'I am walking with Lev.'),
                            ('Мясо готовят на пару.', 'The meat is steamed.'),
                            ('А то что?', 'Or else what?')]:
            t._validate_participant_roles(source,good,'ru','en')

    def test_emotional_metaphor_guidance_does_not_erase_literal_danger(self):
        t=LocalTranslator('http://localhost:11434')
        prompt=t._prompt('Он меня топит.','ru','en')
        self.assertIn('makes me melt',prompt)
        self.assertIn('With real water, submersion or danger preserve drowning',prompt)

    def test_numeric_approximation_tilde_is_not_a_clause_boundary(self):
        for gap in ('', ' '):
            t=LocalTranslator('http://localhost:11434')
            t._request=Mock(return_value=json.dumps({'part_0':'Привет.',
                'part_1':'Вы будете свободны через [[DCTOKEN_0]]'+gap+'5 минут?'}))
            result=t.translate_to('Hi. ~ Are you available in ~'+gap+'5 minutes?', 'ru', 'en')
            self.assertEqual(result.text, 'Привет. ~ Вы будете свободны через ~'+gap+'5 минут?')
            self.assertNotIn('part_2',t._request.call_args.args[0])
            self.assertEqual(t._request.call_count,1)

    def test_numbered_handle_does_not_dominate_short_status_language(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value=json.dumps({'part_1':'В отпуске.'}))
        self.assertEqual(t.translate_to('RANGER42 ~ On leave.','ru','en').text,'RANGER42 ~ В отпуске.')
        t._request.assert_called_once()
        self.assertEqual(direction_for('RANGER42 ~ В отпуске.','auto'),('ru','en'))
        self.assertIsNone(direction_for('RANGER42','auto'))
        t._validate_output('RANGER42 ~ On leave.','RANGER42 ~ В отпуске.','ru','en')
        with self.assertRaises(TranslationUnavailable):
            t._validate_output('RANGER42 ~ Currently on leave.','RANGER42 ~ Currently on leave.','ru','en')

    def test_sleepyhead_and_venting_idioms_have_contextual_not_global_name_hints(self):
        t=LocalTranslator('http://localhost:11434')
        self.assertIn('two sleepyheads',t._prompt('Мы с тобой две сони.','ru','en'))
        self.assertIn('actually identifies people called Sonya',t._prompt('Две Сони.','ru','en'))
        self.assertNotIn('two sleepyheads',t._prompt('Соня пришла к нам.','ru','en'))
        self.assertIn('teasing solidarity',t._prompt('Обглодаем кости тому, кто тебя обидел.','ru','en'))
        self.assertNotIn('teasing solidarity',t._prompt('Пёс обглодал кости.','ru','en'))
        self.assertIn('relate to them',t._prompt('Хорошо, что ты понимаешь эти чувства.','ru','en'))
        self.assertNotIn('relate to them',t._prompt('Ты понимаешь правила?','ru','en'))

    def test_placeholder_adjacent_roster_name_is_not_an_added_command(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value=json.dumps({'part_0':'[[DCTOKEN_0]]/[[DCTOKEN_1]].Marius — группа.',
            'part_2':'Держите позиции.'}))
        self.assertEqual(t.translate_to('KayKat/M1K3.Marius group.\n\nHold position.','ru','en').text,
            'KayKat/M1K3.Marius — группа.\n\nДержите позиции.')
        t._request.assert_called_once()

    def test_name_only_emoji_greeting_is_local_and_preserves_punctuation(self):
        t=LocalTranslator('http://localhost:11434'); t._request=Mock()
        self.assertEqual(t.translate_to('....Angelina.. ❤️','ru','en').text,'....Ангелина.. ❤️')
        t._request.assert_not_called()

    def test_emphatic_negation_and_emotional_winding_up_hints(self):
        t=LocalTranslator('http://localhost:11434')
        self.assertIn('not at all',t._prompt('Я не механик ни разу.','ru','en'))
        self.assertIn('got into their head',t._prompt('Он накрутил человека.','ru','en'))
        t._request=Mock(side_effect=['I am not a mechanic, not even once.','I am no mechanic at all.'])
        with self.assertLogs('translator_bot',level='WARNING'):
            self.assertEqual(t.translate_to('Я не механик ни разу.','en','ru').text,'I am no mechanic at all.')
        self.assertEqual(t._request.call_count,2)
        t._validate_participant_roles('Я не выступал ни разу.','I have not performed even once.','ru','en')

    def test_revision_prompt_audits_untrusted_previous_translation(self):
        t=LocalTranslator('http://localhost:11434')
        with t.conversation_context(json.dumps({'originals':[], 'previous_translation':'bad'})):
            self.assertIn('requested revision',t._prompt('Hello.','en','ru'))
    def test_latin_quote_signatures_do_not_determine_russian_language(self):
        source = '«Привет, мы обсуждаем правила нашего союза.» - Alpha\n\n“Мы поговорим с обоими игроками.” - beta\n\n“Это вопрос к одному игроку.” - gamma'
        self.assertEqual(detect_language(source),'ru')
        self.assertEqual(direction_for(source,'auto'),('ru','en'))
        self.assertEqual(detect_language('«Это русский вопрос.»\nI disagree with that conclusion.'),'en')

    def test_emoji_copied_from_reference_is_rejected_and_retried(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(side_effect=['Has mine put up a shield? Good. 🥰😊', 'Has mine put up a shield?'])
        with self.assertLogs('translator_bot',level='WARNING'):
            result=t.translate_to('Мой поставил щит?)','en','ru')
        self.assertEqual(result.text,'Has mine put up a shield? 🙂')
        self.assertEqual(t._request.call_count,2)

    def test_numb_arm_and_chat_typo_hints_are_source_matched(self):
        t=LocalTranslator('http://localhost:11434')
        self.assertIn('went numb',t._prompt('Я отлежала руку.','ru','en'))
        self.assertIn('not wallpapers',t._prompt('Они переговорили с обоями.','ru','en'))
        self.assertNotIn('not wallpapers',t._prompt('Стена с обоями.','ru','en'))

    def test_structured_prompt_forbids_added_italic_actions(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value=json.dumps({'part_0':'Ahem.', 'part_2':'An update.'}))
        self.assertEqual(t.translate_to('Кхм.\n\nОбновление.','en','ru').text,'Ahem.\n\nAn update.')
        self.assertIn('plain ahem',t._request.call_args.args[0])

    def test_unmatched_opening_bracket_is_preserved(self):
        t = LocalTranslator('http://localhost:11434')
        t._request = Mock(return_value='Tell me when to stop.')
        self.assertEqual(t.translate_to('[Скажи мне, когда остановиться.', 'en', 'ru').text,
                         '[Tell me when to stop.')
        t._request.assert_called_once()

    def test_cross_line_copied_clause_is_retried_within_budget(self):
        t = LocalTranslator('http://localhost:11434')
        t._request = Mock(side_effect=[
            json.dumps({'part_0':'This can wait. I will finish the report and return to you.','part_1':'I will finish the report and return to you.'}),
            json.dumps({'part_0':'This story can wait.','part_1':'I will finish the report and return to you.'})])
        with self.assertLogs('translator_bot', level='WARNING'):
            result = t.translate_to('Эта история подождёт.\nДоделаю отчёт и вернусь к тебе.', 'en', 'ru')
        self.assertEqual(result.text, 'This story can wait.\nI will finish the report and return to you.')
        self.assertEqual(t._request.call_count, 2)
        t._validate_layout_overlap('Я обещаю это сделать для тебя завтра.\nЯ обещаю это сделать для тебя завтра.',
            'I promise to do this for you tomorrow.\nI promise to do this for you tomorrow.')

    def test_source_matched_idiom_and_pronoun_guidance(self):
        t = LocalTranslator('http://localhost:11434')
        self.assertIn('not all people', t._prompt('Cannot do it all at once.', 'en', 'ru'))
        self.assertIn('Preserve realization', t._prompt('Вот это твой секрет.', 'ru', 'en'))
        self.assertIn('without adding a possessive', t._prompt('Госпожа желает.', 'ru', 'en'))
        self.assertIn('not the insect', t._prompt('Ты накуралесил. Жук.', 'ru', 'en'))
        self.assertNotIn('not the insect', t._prompt('Это жук на дереве.', 'ru', 'en'))

    def test_emoji_line_retry_freezes_order_count_and_newlines(self):
        t = LocalTranslator('http://localhost:11434')
        prose = iter(['Thanks.', 'I studied physics.'])
        def request(prompt, **kwargs):
            if t._request.call_count == 1:
                return json.dumps({'part_0':'Thanks.', 'part_1':'I studied physics.'})
            return json.dumps({key: field['const'] if 'const' in field else next(prose)
                for key, field in kwargs['format_schema']['properties'].items()})
        t._request = Mock(side_effect=request)
        with self.assertLogs('translator_bot', level='WARNING'):
            result = t.translate_to('Спасибо. 😂\nЯ изучала физику. 😆😆😆😆', 'en', 'ru')
        self.assertEqual(result.text, 'Thanks. 😂\nI studied physics. 😆😆😆😆')
        self.assertEqual(t._request.call_count, 2)

    def test_missing_middle_line_heart_cannot_be_published(self):
        t = LocalTranslator('http://localhost:11434')
        prose = iter(['Hello.', 'Thanks.', 'It is mutual.'])
        def request(prompt, **kwargs):
            if t._request.call_count == 1:
                return json.dumps({'part_0':'Hello. [[DCTOKEN_0]]', 'part_1':'Thanks.', 'part_2':'It is mutual.'})
            return json.dumps({key: field['const'] if 'const' in field else next(prose)
                for key, field in kwargs['format_schema']['properties'].items()})
        t._request = Mock(side_effect=request)
        with self.assertLogs('translator_bot', level='WARNING'):
            result = t.translate_to('Привет. 🙈\nСпасибо. 💔\nЭто взаимно.', 'en', 'ru')
        self.assertEqual(result.text, 'Hello. 🙈\nThanks. 💔\nIt is mutual.')
        self.assertEqual(t._request.call_count, 2)

    def test_identity_clarification_retains_who_not_someone(self):
        t = LocalTranslator('http://localhost:11434')
        t._request = Mock(side_effect=['Are you talking about someone?', 'Who are you talking about?'])
        self.assertEqual(t.translate_to('Ты про кого?', 'en', 'ru').text, 'Who are you talking about?')
        self.assertEqual(t._request.call_count, 2)
        t._validate_participant_roles('Я говорил про кого-то.', 'I was talking about someone.', 'ru', 'en')
        t._validate_participant_roles('Ты про кого?', 'Which person do you mean?', 'ru', 'en')

    def test_compound_cyrillic_player_names_are_fixed_identifiers(self):
        original = 'СеверЛис и ТеньВолк присоединились.'
        text, protected = LocalTranslator._protect(original)
        self.assertEqual(list(protected.values()), ['СеверЛис', 'ТеньВолк'])
        self.assertEqual(LocalTranslator._restore(text, protected), original)
        LocalTranslator._validate_output(original, 'СеверЛис and ТеньВолк have joined.', 'en', 'ru')
        self.assertEqual(LocalTranslator._protect('Мы говорим про икры.')[1], {})

    def test_body_food_hug_role_and_negation_hints_are_source_matched(self):
        t = LocalTranslator('http://localhost:11434')
        for source, hint in [('Икры болят.', 'lower-leg muscles'),
                             ('Банка икры.', 'food or fish'),
                             ('Сгребёшь меня в объятия?', 'Directional'),
                             ('Кого поставить на титул обаятеля?', 'Charmer'),
                             ('Когда ты не будешь свободен.', 'Negated future')]:
            self.assertIn(hint, t._prompt(source, 'ru', 'en'))
        self.assertNotIn('lower-leg muscles', t._prompt('Привет.', 'ru', 'en'))

    def test_affirmative_statement_cannot_become_a_question(self):
        t = LocalTranslator('http://localhost:11434')
        t._request = Mock(side_effect=[json.dumps({'part_0':'Yes, I am here.','part_1':'Did you save my friend?'}),
                                      json.dumps({'part_0':'Yes, I am here.','part_1':'You saved my friend.'})])
        self.assertEqual(t.translate_to('Да, я тут.\nТы спас моего друга.', 'en', 'ru').text,
                         'Yes, I am here.\nYou saved my friend.')
        self.assertEqual(t._request.call_count, 2)
        LocalTranslator._validate_participant_roles('Да, я тут.\nТы спас моего друга?',
            'Yes, I am here.\nDid you save my friend?', 'ru', 'en')

    def test_unchanged_identifier_comparison_has_no_translation(self):
        t = LocalTranslator('http://localhost:11434')
        t._request = Mock(return_value='"Alaric"? Alarik?')
        self.assertIsNone(t.translate_to('"Alaric" ? Alarik?', 'ru', 'en'))
        self.assertEqual(t._request.call_count, 1)
        self.assertFalse(t._is_identifier_comparison('"Hello"? What?'))
        self.assertFalse(t._is_identifier_comparison('"Alaric"? Do you mean Alarik?'))
        with self.assertRaises(TranslationUnavailable):
            t._validate_output('Please save my friend.', 'Please save my friend.', 'ru', 'en')

    def test_common_prompt_stays_compact_and_narrative_rules_are_relevant_only(self):
        t = LocalTranslator('http://localhost:11434')
        ordinary = t._prompt('Hello, how are you?', 'en', 'ru')
        self.assertLess(len(ordinary), 2500)
        self.assertNotIn('Omitted-subject participles', ordinary)
        self.assertIn('Omitted-subject participles', t._prompt('*Я повернулась к нему.*', 'ru', 'en'))
        self.assertIn('Never censor, sanitize, soften', ordinary)
        self.assertIn('Resolve omitted subjects', ordinary)

    def test_literal_syntax_instruction_survives_prompt_compaction(self):
        t = LocalTranslator('http://localhost:11434')
        t._request = Mock(return_value='<@123> Используйте /fix.')
        self.assertEqual(t.translate_to('<@123> Use /fix.', 'ru', 'en').text,
                         '<@123> Используйте /fix.')
        self.assertIn('Copy all literal Markdown delimiters', t._request.call_args.args[0])
        self.assertEqual(t._request.call_count, 1)

    def test_explicit_body_owner_is_not_swapped(self):
        for source, wrong in [('Моя шея болит.', 'Your neck hurts.'),
                              ('Твоё тело устало.', 'My body is tired.')]:
            with self.assertRaises(TranslationUnavailable):
                LocalTranslator._validate_participant_roles(source, wrong, 'ru', 'en')
        LocalTranslator._validate_participant_roles('Моя шея болит.', 'My neck hurts.', 'ru', 'en')

    def test_ambiguous_or_mixed_owners_are_not_guessed(self):
        for source, output in [('Шея болит.', 'Your neck hurts.'),
                               ('Моя шея и твоя шея.', 'My neck and your neck.'),
                               ('Мой телефон.', 'Your body.'),
                               ('Моё шептание.', 'Your neck.')]:
            LocalTranslator._validate_participant_roles(source, output, 'ru', 'en')

    def test_role_drift_reuses_existing_retry_budget(self):
        t = LocalTranslator('http://localhost:11434')
        t._request = Mock(side_effect=['Your neck hurts.', 'My neck hurts.'])
        self.assertEqual(t.translate_to('Моя шея болит.', 'en', 'ru').text, 'My neck hurts.')
        self.assertEqual(t._request.call_count, 2)
        t.clear_cache()
        t._request = Mock(return_value='Your neck hurts.')
        with self.assertRaises(TranslationUnavailable):
            t.translate_to('Моя шея болит.', 'en', 'ru')
        self.assertEqual(t._request.call_count, 2)

    def test_action_narrator_survives_possessive_not_actual_subject_change(self):
        source = '*Я взяла его сумку.* Хорошо. *Толкнув дверь, вошла внутрь.*'
        correct = '*I took his bag.* Okay. *Pushing the door, I went inside.*'
        LocalTranslator._validate_participant_roles(source, correct, 'ru', 'en')
        with self.assertRaises(TranslationUnavailable):
            LocalTranslator._validate_participant_roles(source,
                '*I took his bag.* Okay. *He pushed the door and went inside.*', 'ru', 'en')
        LocalTranslator._validate_participant_roles(
            '*Я взяла сумку.* Хорошо. *Он толкнул дверь.*',
            '*I took the bag.* Okay. *He pushed the door.*', 'ru', 'en')
        LocalTranslator._validate_participant_roles(
            '*Я говорила, а он взял сумку.* *Толкнув дверь, вошёл внутрь.*',
            '*I was talking and he took the bag.* *He pushed the door and went inside.*', 'ru', 'en')

    def test_stretched_exclamations_are_not_partial_www_urls(self):
        for source in ('Owww... That sounds painful.', 'Awww... Poor thing.', 'www...'):
            self.assertEqual(LocalTranslator._protect(source)[1], {})
        for source in ('See www.example.com/path', '(https://example.com)', 'www.пример.рф'):
            self.assertTrue(LocalTranslator._protect(source)[1])
        t = LocalTranslator('http://localhost:11434')
        t._request = Mock(return_value='Ох... Бедняжка...')
        self.assertEqual(t.translate_to('Owww... Poor thing...', 'ru', 'en').text, 'Ох... Бедняжка...')
        t._request.assert_called_once()
    def test_inline_command_names_are_identifiers_not_commands_to_execute(self):
        source = 'Use "/repair" or "-reconnect" to try again.'
        text, protected = LocalTranslator._protect(source)
        self.assertEqual(list(protected.values()), ['/repair', '-reconnect'])
        self.assertEqual(LocalTranslator._restore(text, protected), source)
        self.assertIsNotNone(direction_for(source, 'auto'))
        self.assertIsNone(direction_for('/repair', 'auto'))
        self.assertEqual(LocalTranslator._protect('A well-known singer')[1], {})
        self.assertEqual(LocalTranslator._protect('Path /usr/bin')[1], {})

    def test_command_and_mention_failure_uses_fixed_segments_with_two_calls(self):
        t = LocalTranslator('http://localhost:11434')
        def respond(prompt, **kwargs):
            if 'format_schema' not in kwargs:
                return 'Попробуйте /починить.'
            fields = {}
            for key, schema in kwargs['format_schema']['properties'].items():
                fields[key] = schema['const'] if 'const' in schema else 'Попробуйте'
            return json.dumps(fields)
        t._request = Mock(side_effect=respond)
        output = t.translate_to('<@123> Try /repair', 'ru', 'en').text
        self.assertIn('<@123>', output)
        self.assertIn('/repair', output)
        self.assertEqual(t._request.call_count, 2)

    def test_unquoted_reply_selects_direction_not_foreign_quote(self):
        text = '"Это очень длинная русская цитата, которая относится к предыдущему сообщению." ~? What do you mean?'
        self.assertEqual(direction_for(text, 'auto'), ('en', 'ru'))
        self.assertEqual(direction_for('"Это русская цитата."', 'auto'), ('ru', 'en'))
        self.assertEqual(direction_for('"Это русская цитата." R4', 'auto'), ('ru', 'en'))
        self.assertEqual(direction_for('"An English quote." Что именно?', 'auto'), ('ru', 'en'))

    def test_music_credit_does_not_hide_wrong_language_prose(self):
        source = 'I love this song so much\nAurora - Moonlight'
        LocalTranslator._validate_output(source, 'Я обожаю эту песню\nAurora - Moonlight', 'ru', 'en')
        with self.assertRaises(TranslationUnavailable):
            LocalTranslator._validate_output(source, source, 'ru', 'en')
        with self.assertRaises(TranslationUnavailable):
            LocalTranslator._validate_output('Review this product\nAurora - Moonlight',
                'Вот обзор\nAurora - Moonlight', 'ru', 'en')
        with self.assertRaises(TranslationUnavailable):
            LocalTranslator._validate_output(source, 'Я люблю\nSomeone - Different', 'ru', 'en')

    def test_emojis_cannot_be_replaced_by_another_expression(self):
        t = LocalTranslator('http://localhost:11434')
        def respond(prompt, **kwargs):
            if 'format_schema' not in kwargs:
                return 'I only hid the toy 😉'
            fields = {}
            for key, schema in kwargs['format_schema']['properties'].items():
                fields[key] = schema['const'] if 'const' in schema else 'I only hid the toy'
            return json.dumps(fields)
        t._request = Mock(side_effect=respond)
        self.assertEqual(t.translate_to('Только игрушку спрятала 🤭', 'en', 'ru').text, 'I only hid the toy 🤭')
        self.assertEqual(t._request.call_count, 2)

    def test_unicode_emoji_sequences_preserved_as_whole_tokens(self):
        for emoji in ('🤭', '❤️', '👩🏽‍💻', '🇬🇧', '1️⃣'):
            source = 'Hello ' + emoji
            text, protected = LocalTranslator._protect(source)
            self.assertEqual(LocalTranslator._restore(text, protected), source)
            self.assertNotIn(emoji, text)

    def test_surprise_is_not_collapsed_to_an_o_sound(self):
        t = LocalTranslator('http://localhost:11434')
        t._request = Mock(side_effect=AssertionError('Nonlexical chat needs no inference'))
        self.assertEqual(t.translate_to('Огооо!', 'en', 'ru').text, 'Wooow!')
        self.assertEqual(t.translate_to('Оооо.', 'en', 'ru').text, 'Oooo.')
        t._request.assert_not_called()
