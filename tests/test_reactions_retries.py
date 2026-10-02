import tempfile
import time
import unittest
import discord
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from translator_bot.bot import TranslatorBot
from translator_bot.config import Settings
from translator_bot.translation import TranslationResult, TranslationUnavailable, LocalTranslator
from translator_bot.storage import SettingsStore


class ReactionRetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bot = TranslatorBot(Settings('test', 1, Path(self.tmp.name)/'db', 'INFO', 'http://localhost:11434', 'test', 90))
        self.bot._connection.user = SimpleNamespace(id=99)
        self.source = SimpleNamespace(id=30, content='Hello', guild=SimpleNamespace(id=1),
            author=SimpleNamespace(id=40, bot=False, display_name='Alice', display_avatar=SimpleNamespace(url='https://example.com')),
            webhook_id=None, add_reaction=AsyncMock(), remove_reaction=AsyncMock())
        self.post = SimpleNamespace(id=80, webhook_id=70, content='Failure', add_reaction=AsyncMock(), remove_reaction=AsyncMock())
        self.channel = SimpleNamespace(id=20, guild=SimpleNamespace(id=1),
            fetch_message=AsyncMock(side_effect=lambda id: self.source if id==30 else self.post))
        self.source.channel = self.channel
        self.bot.get_channel = lambda id: self.channel
        self.hook = SimpleNamespace(id=70, edit_message=AsyncMock(return_value=self.post),
            send=AsyncMock(return_value=self.post), delete_message=AsyncMock())
        self.bot.translation_webhook = AsyncMock(return_value=self.hook)
        self.bot.store.link_translation(80, 30, 20, 70, 'en', 'ru')
        self.payload = SimpleNamespace(message_id=80, channel_id=20, guild_id=1, user_id=50, emoji='❤️')

    async def asyncTearDown(self):
        await self.bot.close()
        self.tmp.cleanup()

    async def test_reactions_mirror_once_and_last_removal_clears_bot_reaction(self):
        await self.bot.on_raw_reaction_add(self.payload)
        self.payload.user_id=51
        await self.bot.on_raw_reaction_add(self.payload)
        self.source.add_reaction.assert_awaited_with('❤️')
        self.payload.user_id=50
        await self.bot.on_raw_reaction_remove(self.payload)
        self.source.remove_reaction.assert_not_awaited()
        self.payload.user_id=51
        await self.bot.on_raw_reaction_remove(self.payload)
        self.source.remove_reaction.assert_awaited_once()

    async def test_bot_and_untracked_original_reactions_do_not_loop(self):
        self.payload.user_id=99
        await self.bot.on_raw_reaction_add(self.payload)
        self.payload.user_id=50; self.payload.message_id=30
        await self.bot.on_raw_reaction_add(self.payload)
        self.source.add_reaction.assert_not_awaited()

    async def test_custom_emoji_and_clear_are_mirrored(self):
        self.payload.emoji='<:party:123456>'
        await self.bot.on_raw_reaction_add(self.payload)
        await self.bot.on_raw_reaction_clear(self.payload)
        self.source.remove_reaction.assert_awaited_once()
        self.assertEqual(str(self.source.remove_reaction.call_args.args[0]), '<:party:123456>')

    async def test_successful_retry_updates_existing_failure(self):
        self.bot.store.link_translation(80,30,20,70,'en','ru',status='failed')
        self.bot.store.schedule_retry(30,1,20,0)
        self.bot.translate_content = Mock(return_value=TranslationResult('en','ru','Привет'))
        await self.bot.retry_failures()
        self.hook.edit_message.assert_awaited_once()
        self.hook.send.assert_not_awaited()
        self.assertEqual(self.bot.store.get_translation_link(80)['status'], 'complete')
        self.assertEqual(self.bot.store.due_retries(1e20), [])

    async def test_interrupted_pending_post_can_be_recovered_in_place(self):
        self.bot.store.link_translation(80,30,20,70,'en','ru',status='pending')
        self.bot.store.schedule_retry(30,1,20,0)
        self.bot.translate_content=Mock(return_value=TranslationResult('en','ru','Привет'))
        await self.bot.retry_failures()
        self.hook.edit_message.assert_awaited_once()
        self.hook.send.assert_not_awaited()
        self.assertEqual(self.bot.store.get_translation_link(80)['status'],'complete')

    async def test_ready_schedules_persisted_pending_placeholder(self):
        self.bot.store.link_translation(80,30,20,70,'en','ru',status='pending')
        self.bot.expire_temporary.is_running=Mock(return_value=True)
        self.bot.retry_failures.is_running=Mock(return_value=True)
        await self.bot.on_ready()
        self.assertEqual(self.bot.store.due_retries(time.time()+6)[0]['source_id'],30)

    async def test_verified_repair_clears_flags_on_original_and_translation(self):
        self.payload.emoji='🚩'; self.payload.message_id=30
        await self.bot.on_raw_reaction_add(self.payload)
        self.payload.message_id=80
        await self.bot.on_raw_reaction_add(self.payload)
        self.post.content='Привет'
        with self.bot.store._connection:
            self.bot.store._connection.execute("UPDATE translation_feedback SET status='applied',corrected_text='Привет'")
        await self.bot.retry_failures()
        self.source.remove_reaction.assert_awaited_once()
        self.post.remove_reaction.assert_awaited_once()
        self.post.add_reaction.assert_awaited_once_with('🔄')

    async def test_original_flags_are_logged_against_existing_translation(self):
        self.payload.message_id=30; self.payload.emoji='🚩'
        await self.bot.on_raw_reaction_add(self.payload)
        row=self.bot.store._connection.execute('SELECT * FROM translation_feedback').fetchone()
        self.assertEqual(row['translation_message_id'],80)
        self.assertEqual(self.bot.store.feedback_flag_messages(row['id']),[30])

    async def test_original_flag_recovers_missing_translation_and_records_report(self):
        self.bot.store.unlink_translation(80)
        self.payload.message_id=30; self.payload.emoji='🚩'
        await self.bot.on_raw_reaction_add(self.payload)
        self.hook.send.assert_awaited_once()
        self.assertEqual(self.bot.store.get_translation_link(80)['status'],'pending')
        self.assertEqual(self.bot.store.open_feedback_count(1),1)
        self.assertEqual(self.bot.store.due_retries(time.time()+1)[0]['source_id'],30)

    async def test_original_flag_cannot_enable_translation_in_disabled_channel(self):
        self.bot.store.unlink_translation(80)
        self.bot.store.set_channel_enabled(1,20,False)
        self.payload.message_id=30; self.payload.emoji='🚩'
        await self.bot.on_raw_reaction_add(self.payload)
        self.hook.send.assert_not_awaited()

    async def test_flags_on_both_source_and_translation_share_report_until_both_removed(self):
        self.payload.emoji='🚩'; self.payload.message_id=30
        await self.bot.on_raw_reaction_add(self.payload)
        self.payload.message_id=80
        await self.bot.on_raw_reaction_add(self.payload)
        self.assertEqual(self.bot.store.open_feedback_count(1),1)
        self.payload.message_id=30
        await self.bot.on_raw_reaction_remove(self.payload)
        self.assertEqual(self.bot.store.open_feedback_count(1),1)
        self.payload.message_id=80
        await self.bot.on_raw_reaction_remove(self.payload)
        self.assertEqual(self.bot.store.open_feedback_count(1),0)

    async def test_persistent_failure_stops_after_three_automatic_attempts(self):
        self.bot.store.link_translation(80,30,20,70,'en','ru',status='failed')
        self.bot.store.schedule_retry(30,1,20,0)
        self.bot.translate_content=Mock(side_effect=TranslationUnavailable('offline'))
        for _ in range(4):
            with self.bot.store._connection:
                self.bot.store._connection.execute('UPDATE translation_retries SET next_at=0')
            await self.bot.retry_failures()
        self.assertEqual(self.bot.translate_content.call_count,3)
        self.hook.edit_message.assert_not_awaited()

    async def test_retry_cannot_publish_after_original_changes(self):
        self.bot.store.link_translation(80,30,20,70,'en','ru',status='failed')
        self.bot.store.schedule_retry(30,1,20,0)
        def generate(*args):
            self.source.content='Updated'
            return TranslationResult('en','ru','Старое')
        self.bot.translate_content=Mock(side_effect=generate)
        await self.bot.retry_failures()
        self.hook.edit_message.assert_not_awaited()

    async def test_on_demand_failure_retries_without_extra_posts(self):
        self.bot.on_demand_retry_delays=(0,0,0)
        self.bot.translator.translate_to=Mock(side_effect=[TranslationUnavailable('offline'),TranslationResult('en','ru','Привет')])
        result=await self.bot.translate_on_demand('Hello','ru','en')
        self.assertEqual(result.text,'Привет')
        self.assertEqual(self.bot.translator.translate_to.call_count,2)

    async def test_retry_and_mirror_memberships_survive_restart(self):
        path=Path(self.tmp.name)/'db'
        self.bot.store.schedule_retry(30,1,20,0)
        self.bot.store.mirror_reaction(80,30,20,'❤️',50)
        self.bot.store.close()
        self.bot.store=SettingsStore(path)
        self.assertEqual(self.bot.store.due_retries(time.time())[0]['source_id'],30)
        removed=self.bot.store.unmirror_reaction(80,'❤️',50)
        self.assertEqual(removed[0]['source_id'],30)

    async def test_disabled_channel_cancels_automatic_retry(self):
        self.bot.store.link_translation(80,30,20,70,'en','ru',status='failed')
        self.bot.store.schedule_retry(30,1,20,0)
        self.bot.store.set_channel_enabled(1,20,False)
        self.bot.translate_content=Mock()
        await self.bot.retry_failures()
        self.bot.translate_content.assert_not_called()
        self.assertEqual(self.bot.store.due_retries(1e20),[])

    async def test_applied_report_cleanup_keeps_reviewed_record_and_can_be_reflagged(self):
        values=dict(guild_id=1,channel_id=20,source_message_id=30,translation_message_id=80,
            source_author_id=40,reporter_id=50,source_language='en',target_language='ru',
            source_text='Hello',translated_text='Wrong',corrected_text=None,note=None)
        self.bot.store.add_feedback(**values)
        with self.bot.store._connection:
            self.bot.store._connection.execute("UPDATE translation_feedback SET status='applied',corrected_text='Привет'")
        self.post.content='Привет'
        await self.bot.retry_failures()
        self.post.add_reaction.assert_awaited_once_with('🔄')
        self.post.remove_reaction.assert_awaited_once()
        self.assertFalse(self.bot.store.remove_feedback(80,50))
        self.assertTrue(self.bot.store.add_feedback(**values))
        self.assertEqual(self.bot.store._connection.execute('SELECT count(*) FROM feedback_history').fetchone()[0],1)

    async def test_repair_status_reaction_is_not_mirrored_or_a_request(self):
        self.payload.emoji='🔄'
        await self.bot.on_raw_reaction_add(self.payload)
        await self.bot.on_raw_reaction_remove(self.payload)
        self.source.add_reaction.assert_not_awaited()
        self.hook.send.assert_not_awaited()

    async def test_changed_post_is_not_marked_as_old_reviewed_repair(self):
        self.bot.store.add_feedback(guild_id=1,channel_id=20,source_message_id=30,
            translation_message_id=80,source_author_id=40,reporter_id=50,
            source_language='en',target_language='ru',source_text='Hello',
            translated_text='Wrong',corrected_text='Привет',note=None)
        with self.bot.store._connection:
            self.bot.store._connection.execute("UPDATE translation_feedback SET status='applied'")
        await self.bot.retry_failures()
        self.post.add_reaction.assert_not_awaited()
        self.post.remove_reaction.assert_not_awaited()

    async def test_repair_marker_failure_keeps_flag_and_retries_later(self):
        self.bot.store.add_feedback(guild_id=1,channel_id=20,source_message_id=30,
            translation_message_id=80,source_author_id=40,reporter_id=50,
            source_language='en',target_language='ru',source_text='Hello',
            translated_text='Wrong',corrected_text='Привет',note=None)
        with self.bot.store._connection:
            self.bot.store._connection.execute("UPDATE translation_feedback SET status='applied'")
        self.post.content='Привет'
        self.post.add_reaction.side_effect=discord.Forbidden(SimpleNamespace(status=403,reason='Forbidden'),'Missing permission')
        with self.assertLogs('translator_bot',level='WARNING'):
            await self.bot.retry_failures()
        self.post.remove_reaction.assert_not_awaited()
        self.assertEqual(len(self.bot.store.corrected_flags_due(time.time()+301)),1)


class LayoutTests(unittest.TestCase):
    def test_internal_blank_lines_and_whitespace_only_lines_use_structured_layout(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value='{"part_0":"Первая строка.","part_1":"","part_2":" ","part_3":"Последняя строка."}')
        result=t.translate_to('First line.\n\n \nLast line.','ru','en')
        self.assertEqual(result.text,'Первая строка.\n\n \nПоследняя строка.')
        schema=t._request.call_args.kwargs['format_schema']
        self.assertEqual(schema['properties']['part_1']['const'],'')
        self.assertEqual(schema['properties']['part_2']['const'],' ')
        t._request.assert_called_once()

    def test_structured_layout_rejects_missing_extra_empty_or_multiline_fields(self):
        for output in ('{"part_0":"Привет"}',
                       '{"part_0":"Привет","part_1":"Мир","part_2":"Лишнее"}',
                       '{"part_0":"Привет","part_1":""}',
                       '{"part_0":"Привет\\nМир","part_1":"Мир"}',
                       '{"part_0":"Привет ~","part_1":"Мир"}'):
            with self.subTest(output=output):
                t=LocalTranslator('http://localhost:11434')
                t._request=Mock(return_value=output)
                with self.assertRaises(TranslationUnavailable):
                    t.translate_to('Hello ~ world','ru','en')

    def test_structured_layout_does_not_allow_tokens_to_move_between_fields(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value='{"part_0":"Привет","part_1":"[[DCTOKEN_0]] мир"}')
        with self.assertRaises(TranslationUnavailable):
            t.translate_to('Hello <@123> ~ world','ru','en')

    def test_structured_empty_field_preserves_adjacent_tildes(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value='{"part_0":"Подожди","part_1":"","part_2":"что?"}')
        self.assertEqual(t.translate_to('Wait ~ ~ what?','ru','en').text,'Подожди ~ ~ что?')

    def test_multiple_sentences_on_one_source_line_stay_together(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value="I'm not familiar with Discord. It works through a VPN.\nOnly on my phone.")
        result=t.translate_to('Я не знаком с Дискордом. Он работает через VPN.\nТолько на телефоне.','en','ru')
        self.assertEqual(result.text.count('\n'),1)
        prompt=t._request.call_args.args[0]
        self.assertIn('Return exactly 2 translated lines',prompt)
        self.assertIn('do not put individual sentences on separate lines',prompt)

    def test_multiple_inline_tildes_restore_exact_spacing_and_real_newlines(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value='{"part_0":"Первое","part_1":"второе","part_2":"третье.","part_3":"Следующее","part_4":"последнее."}')
        result=t.translate_to('First ~ second~third.\nNext  ~  last.','ru','en')
        self.assertEqual(result.text,'Первое ~ второе~третье.\nСледующее  ~  последнее.')
        t._request.assert_called_once()

    def test_inline_tilde_inside_url_or_code_is_not_a_layout_boundary(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value='{"part_0":"Смотри [[DCTOKEN_0]] и [[DCTOKEN_1]]","part_1":"потом продолжай."}')
        result=t.translate_to('See https://example.com/~me and `a~b` ~ then continue.','ru','en')
        self.assertEqual(result.text,'Смотри https://example.com/~me и `a~b` ~ потом продолжай.')
        t._request.assert_called_once()

    def test_trailing_tilde_is_reattached_without_splitting_context(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value='Привет.\nКак дела?')
        result=t.translate_to('Hello. ~\nHow are you?','ru','en')
        self.assertEqual(result.text,'Привет. ~\nКак дела?')
        t._request.assert_called_once()

    def test_italic_delimiters_and_apostrophes_are_preserved(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value="*I wasn\\'t asleep.* I waited.")
        result=t.translate_to('*Я не спала.* Я ждала.','en','ru')
        self.assertEqual(result.text,"*I wasn't asleep.* I waited.")

    def test_spoken_words_stay_outside_roleplay_italics(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value='*I fell asleep, murmuring, "I am waiting for you."*')
        result=t.translate_to('*Я уснула, пробормотав* я жду тебя.','en','ru')
        self.assertEqual(result.text,'*I fell asleep, murmuring* I am waiting for you.')

    def test_tilde_only_lines_keep_their_positions(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value='Привет.')
        self.assertEqual(t.translate_to('~\nHello.\n~','ru','en').text,'~\nПривет.\n~')

    def test_line_prefix_tildes_and_emoji_do_not_depend_on_neural_copying(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value='Ты виноват. 🤣\nКакое расписание?')
        result=t.translate_to('😏 ~ Your fault. 🤣\n~What is the schedule?','ru','en')
        self.assertEqual(result.text,'😏 ~ Ты виноват. 🤣\n~Какое расписание?')
        t._request.assert_called_once()

    def test_russian_sentence_ending_smiley_becomes_emoji(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value='I hope you slept.')
        self.assertEqual(t.translate_to('Надеюсь, ты спал.)','en','ru').text,'I hope you slept. 🙂')

    def test_russian_question_and_bare_smileys_become_emoji(self):
        for source, translated, expected in (
            ('Ты бариста?)', 'Are you a barista?', 'Are you a barista? 🙂'),
            ('Спасибо))', 'Thank you', 'Thank you 😄'),
            ('Смешно.)))', 'Funny.', 'Funny. 😆'),
            ('Смешно))))))', 'Funny', 'Funny 😆'),
        ):
            t=LocalTranslator('http://localhost:11434')
            t._request=Mock(return_value=translated)
            self.assertEqual(t.translate_to(source,'en','ru').text,expected)

    def test_english_typed_smileys_are_not_changed(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value='Привет.')
        self.assertEqual(t.translate_to('Hello.)','ru','en').text,'Привет.)')

    def test_tilde_before_final_punctuation_preserves_question(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value='Are you coming?')
        self.assertEqual(t.translate_to('Ты идёшь~?','en','ru').text,'Are you coming~?')
        self.assertIn('Ты идёшь?',t._request.call_args.args[0])

    def test_period_on_both_sides_of_trailing_tilde_does_not_duplicate(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value='Продолжим.')
        self.assertEqual(t.translate_to('Let us continue. ~.','ru','en').text,'Продолжим. ~.')
        self.assertNotIn('continue..',t._request.call_args.args[0])

    def test_balanced_parenthesis_is_not_misread_as_a_smiley(self):
        t=LocalTranslator('http://localhost:11434')
        t._request=Mock(return_value='(Как ты любишь кофе?)')
        self.assertEqual(t.translate_to('(How do you like your coffee?)','ru','en').text,'(Как ты любишь кофе?)')
