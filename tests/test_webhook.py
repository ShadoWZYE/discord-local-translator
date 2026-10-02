import tempfile
import asyncio
import threading
import unittest
import discord
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from translator_bot.bot import TranslatorBot, combined_message
from translator_bot.config import Settings
from translator_bot.storage import SettingsStore
from translator_bot.translation import TranslationUnavailable


class WebhookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bot = TranslatorBot(Settings('test', 1, Path(self.tmp.name) / 'test.db', 'INFO',
                                          'http://localhost:11434', 'test', 90,
                                          additional_guild_ids=(2,), default_off_guild_ids=(2,)))
        self.bot.translator.translate = lambda *args: SimpleNamespace(source='en', target='ru', text='Привет')
        self.bot.batch_pause_seconds = 0
        self.hook = SimpleNamespace(id=70, send=AsyncMock(return_value=SimpleNamespace(id=80)),
            edit_message=AsyncMock(return_value=SimpleNamespace(id=80)), delete_message=AsyncMock())
        self.bot.translation_webhook = AsyncMock(return_value=self.hook)
        self.channel = SimpleNamespace(id=20)
        self.message = SimpleNamespace(id=30, guild=SimpleNamespace(id=1), channel=self.channel,
            content='Hello', webhook_id=None, author=SimpleNamespace(id=40, bot=False,
                display_name='Alice', display_avatar=SimpleNamespace(url='https://example.com/avatar.png')))

    async def asyncTearDown(self):
        await self.bot.close()
        self.tmp.cleanup()

    async def test_compact_webhook_and_feedback_survive_restart(self):
        await self.bot.on_message(self.message)
        args, kwargs = self.hook.send.call_args
        self.assertEqual(args, ('Перевожу…',))
        self.assertEqual(self.hook.edit_message.call_args.kwargs['content'], 'Привет')
        self.assertEqual(kwargs['username'], 'Alice')
        self.assertNotIn('reference', kwargs)
        self.assertFalse(kwargs['allowed_mentions'].everyone)
        self.bot.store.close()
        self.bot.store = SettingsStore(Path(self.tmp.name) / 'test.db')
        link = self.bot.store.get_translation_link(80)
        self.assertEqual(link['source_message_id'], 30)
        self.channel.fetch_message = AsyncMock(side_effect=[
            SimpleNamespace(id=80, webhook_id=70, content='Привет'), self.message])
        self.bot.get_channel = lambda _: self.channel
        payload = SimpleNamespace(emoji='🚩', guild_id=1, channel_id=20, message_id=80, user_id=50)
        await self.bot.on_raw_reaction_add(payload)
        self.assertEqual(self.bot.store.open_feedback_count(1), 1)
        await self.bot.on_raw_reaction_remove(payload)
        self.assertEqual(self.bot.store.open_feedback_count(1), 0)

    async def test_webhook_posts_never_retranslate(self):
        self.message.webhook_id = 70
        await self.bot.on_message(self.message)
        self.hook.send.assert_not_awaited()

    async def test_second_server_defaults_off_and_enabling_is_per_channel(self):
        self.message.guild.id = 2
        await self.bot.on_message(self.message)
        self.hook.send.assert_not_awaited()
        self.assertFalse(self.bot.store.is_channel_enabled(2, 999))
        self.bot.store.set_channel_enabled(2, 20, True)
        await self.bot.on_message(self.message)
        self.hook.send.assert_awaited_once()
        self.assertFalse(self.bot.store.is_channel_enabled(2, 999))
        self.assertTrue(self.bot.store.is_channel_enabled(1, 999))

    async def test_unapproved_server_is_ignored_even_if_channel_enabled(self):
        self.message.guild.id = 3
        self.bot.store.set_channel_enabled(3, 20, True)
        await self.bot.on_message(self.message)
        self.hook.send.assert_not_awaited()

    async def test_attachment_only_and_emoji_only_messages_do_not_translate(self):
        self.bot.translator.translate = Mock(side_effect=AssertionError('No translatable text'))
        self.bot.store.set_user_mode(1, 40, 'en_to_ru')
        for content in ('', '   ', '😀', 'https://example.com/clip.gif', '<:party:123456>'):
            self.message.content = content
            self.message.attachments = [SimpleNamespace(filename='picture.png')]
            await self.bot.on_message(self.message)
        self.bot.translator.translate.assert_not_called()
        self.hook.send.assert_not_awaited()

    async def test_original_edit_updates_same_translation_after_restart(self):
        await self.bot.on_message(self.message)
        self.bot.store.close()
        self.bot.store = SettingsStore(Path(self.tmp.name) / 'test.db')
        self.hook.edit_message = AsyncMock(return_value=SimpleNamespace(id=80))
        self.hook.delete_message = AsyncMock()
        self.message.content = 'Hello again'
        self.bot.translator.translate = lambda *args: SimpleNamespace(source='en', target='ru', text='Снова привет')
        self.channel.fetch_message = AsyncMock(return_value=self.message)
        self.bot.get_channel = lambda _: self.channel
        payload = SimpleNamespace(guild_id=1, channel_id=20, message_id=30, data={'content': 'Hello again'})
        await self.bot.on_raw_message_edit(payload)
        self.hook.edit_message.assert_awaited_once()
        self.assertEqual(self.hook.edit_message.call_args.args, (80,))
        self.assertEqual(self.hook.edit_message.call_args.kwargs['content'], 'Снова привет')
        self.assertEqual(self.hook.send.await_count, 1)

    async def test_embed_only_update_is_ignored(self):
        self.bot.get_channel = lambda _: self.channel
        await self.bot.on_raw_message_edit(SimpleNamespace(
            guild_id=1, channel_id=20, message_id=30, data={'embeds': []}))
        self.bot.translation_webhook.assert_not_awaited()

    async def test_original_delete_removes_all_chunks_even_with_channel_disabled(self):
        await self.bot.on_message(self.message)
        self.bot.store.link_translation(81, 30, 20, 70, 'en', 'ru')
        self.bot.store.set_channel_enabled(1, 20, False)
        self.bot.get_channel = lambda _: self.channel
        await self.bot.on_raw_message_delete(SimpleNamespace(guild_id=1, channel_id=20, message_id=30))
        self.assertEqual([call.args[0] for call in self.hook.delete_message.call_args_list], [80, 81])
        self.assertEqual(self.bot.store.get_source_links(30), [])
        self.assertNotIn((1, 20, 40), self.bot._batches)

    async def test_delete_first_of_group_reanchors_surviving_translation_after_restart(self):
        self.bot.store.link_translation(80, 30, 20, 70, 'en', 'ru')
        self.bot.store.add_batch_source(31, 30)
        self.bot.store.close()
        self.bot.store = SettingsStore(Path(self.tmp.name) / 'test.db')
        remaining = SimpleNamespace(**vars(self.message))
        remaining.id, remaining.content = 31, 'Still here'
        self.channel.fetch_message = AsyncMock(return_value=remaining)
        self.bot.get_channel = lambda _: self.channel
        self.hook.send.return_value = SimpleNamespace(id=81)
        self.hook.edit_message.return_value = SimpleNamespace(id=81)
        self.bot.translator.translate = Mock(return_value=SimpleNamespace(source='en', target='ru', text='Я ещё здесь'))
        await self.bot.on_raw_message_delete(SimpleNamespace(guild_id=1, channel_id=20, message_id=30))
        self.hook.delete_message.assert_awaited_once_with(80)
        self.bot.translator.translate.assert_called_once_with('Still here', 'auto')
        self.assertEqual(self.bot.store.get_source_links(31)[0]['translation_message_id'], 81)
        self.assertEqual(self.bot.store.get_source_links(30), [])
        self.assertEqual(self.bot.store.get_batch_sources(31), [31])

    async def test_bulk_delete_whole_group_does_not_translate_or_repost(self):
        self.bot.store.link_translation(80, 30, 20, 70, 'en', 'ru')
        self.bot.store.add_batch_source(31, 30)
        self.bot.get_channel = lambda _: self.channel
        self.bot.translator.translate = Mock()
        await self.bot.on_raw_bulk_message_delete(SimpleNamespace(guild_id=1, channel_id=20, message_ids={30, 31}))
        self.hook.delete_message.assert_awaited_once_with(80)
        self.hook.send.assert_not_awaited()
        self.bot.translator.translate.assert_not_called()
        self.assertEqual(self.bot.store.get_source_links(31), [])

    async def test_deletion_during_inference_never_resurrects_translation(self):
        entered, release = threading.Event(), threading.Event()
        def translate(*args):
            entered.set()
            release.wait(timeout=3)
            return SimpleNamespace(source='en', target='ru', text='Удалённый текст')
        self.bot.translator.translate = Mock(side_effect=translate)
        self.bot.get_channel = lambda _: self.channel
        task = asyncio.create_task(self.bot.on_message(self.message))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            await self.bot.on_raw_message_delete(SimpleNamespace(guild_id=1, channel_id=20, message_id=30))
        finally:
            release.set()
            await task
        self.hook.delete_message.assert_awaited_once_with(80)
        self.hook.edit_message.assert_not_awaited()
        self.assertEqual(self.hook.send.await_count, 1)
        self.assertEqual(self.bot.store.get_source_links(30), [])

    async def test_deleted_translation_event_does_not_delete_original(self):
        await self.bot.on_message(self.message)
        self.bot.get_channel = lambda _: self.channel
        await self.bot.on_raw_message_delete(SimpleNamespace(guild_id=1, channel_id=20, message_id=80))
        self.hook.delete_message.assert_not_awaited()

    async def test_already_missing_translation_is_cleaned_up(self):
        await self.bot.on_message(self.message)
        self.bot.get_channel = lambda _: self.channel
        self.hook.delete_message.side_effect = discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), 'Unknown Message')
        await self.bot.on_raw_message_delete(SimpleNamespace(guild_id=1, channel_id=20, message_id=30))
        self.assertEqual(self.bot.store.get_source_links(30), [])
        self.assertIsNone(self.bot.store.get_batch_id(30))

    async def test_translation_deletion_passes_thread_context(self):
        self.bot.store.link_translation(80, 30, 20, 70, 'en', 'ru')
        thread = Mock(spec=discord.Thread)
        thread.id = 20
        self.bot.get_channel = lambda _: thread
        await self.bot.on_raw_message_delete(SimpleNamespace(guild_id=1, channel_id=20, message_id=30))
        self.hook.delete_message.assert_awaited_once_with(80, thread=thread)

    async def test_deleting_untranslated_source_removes_it_from_live_group(self):
        self.bot.translator.translate = Mock(return_value=None)
        await self.bot.on_message(self.message)
        self.bot.get_channel = lambda _: self.channel
        await self.bot.on_raw_message_delete(SimpleNamespace(guild_id=1, channel_id=20, message_id=30))
        self.assertNotIn((1, 20, 40), self.bot._batches)
        self.assertIsNone(self.bot.store.get_batch_id(30))

    async def test_delete_part_of_group_during_inference_discards_deleted_text(self):
        entered, release = threading.Event(), threading.Event()
        ids = iter([80, 81, 82])
        self.hook.send.side_effect = lambda *args, **kwargs: SimpleNamespace(id=next(ids))
        self.hook.edit_message.side_effect = lambda message_id, **kwargs: SimpleNamespace(id=message_id)
        await self.bot.on_message(self.message)
        second = SimpleNamespace(**vars(self.message))
        second.id, second.content = 31, 'Surviving text'
        def translate(text, mode):
            if text == 'Hello Surviving text':
                entered.set()
                release.wait(timeout=3)
                return SimpleNamespace(source='en', target='ru', text='Устаревший текст')
            return SimpleNamespace(source='en', target='ru', text='Оставшийся текст')
        self.bot.translator.translate = Mock(side_effect=translate)
        self.bot.get_channel = lambda _: self.channel
        task = asyncio.create_task(self.bot.on_message(second))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            await self.bot.on_raw_message_delete(SimpleNamespace(guild_id=1, channel_id=20, message_id=30))
        finally:
            release.set()
            await task
        self.assertEqual(self.bot.translator.translate.call_args.args[0], 'Surviving text')
        self.assertEqual(self.bot.store.get_source_links(31)[0]['translation_message_id'], 82)
        self.assertEqual(self.bot.store.get_source_links(30), [])
        self.assertFalse(any(call.kwargs.get('content') == 'Устаревший текст'
                             for call in self.hook.edit_message.call_args_list))

    async def test_delete_during_batch_wait_does_not_start_inference(self):
        self.bot.batch_pause_seconds = 1
        self.bot.get_channel = lambda _: self.channel
        self.bot.translator.translate = Mock()
        sent = asyncio.Event()
        async def post(*args, **kwargs):
            sent.set()
            return SimpleNamespace(id=80)
        self.hook.send.side_effect = post
        task = asyncio.create_task(self.bot.on_message(self.message))
        await asyncio.wait_for(sent.wait(), timeout=2)
        await self.bot.on_raw_message_delete(SimpleNamespace(guild_id=1, channel_id=20, message_id=30))
        await task
        self.bot.translator.translate.assert_not_called()
        self.hook.edit_message.assert_not_awaited()

    async def test_shorter_translation_removes_surplus_chunk(self):
        self.bot.store.link_translation(80, 30, 20, 70, 'en', 'ru')
        self.bot.store.link_translation(81, 30, 20, 70, 'en', 'ru')
        self.hook.edit_message = AsyncMock(return_value=SimpleNamespace(id=80))
        self.hook.delete_message = AsyncMock()
        await self.bot.publish_translation(self.message, SimpleNamespace(source='en', target='ru', text='Привет'))
        self.hook.delete_message.assert_awaited_once_with(81)
        self.assertIsNone(self.bot.store.get_translation_link(81))
        self.hook.send.assert_not_awaited()

    async def test_placeholder_is_posted_while_gpu_queue_is_busy(self):
        sent = asyncio.Event()
        async def post(*args, **kwargs):
            sent.set()
            return SimpleNamespace(id=80)
        self.hook.send.side_effect = post
        await self.bot._translation_slots.acquire()
        task = asyncio.create_task(self.bot.on_message(self.message))
        try:
            await asyncio.wait_for(sent.wait(), timeout=2)
            self.assertEqual(self.hook.send.call_args.args, ('Перевожу…',))
            self.hook.edit_message.assert_not_awaited()
            self.assertFalse(task.done())
        finally:
            self.bot._translation_slots.release()
            await task
        self.hook.edit_message.assert_awaited_once()
        self.assertEqual(self.hook.edit_message.call_args.args, (80,))
        self.assertEqual(self.bot.store.get_translation_link(80)['status'], 'complete')

    async def test_failed_translation_replaces_placeholder(self):
        self.bot.translator.translate = Mock(side_effect=TranslationUnavailable('offline'))
        with self.assertLogs('translator_bot', level='ERROR'):
            await self.bot.on_message(self.message)
        self.assertIn('Translation unavailable', self.hook.edit_message.call_args.kwargs['content'])
        self.assertEqual(self.bot.store.get_translation_link(80)['status'], 'failed')

    async def test_failed_russian_source_uses_russian_error_not_english_target(self):
        self.message.content = 'Привет, как дела?'
        self.bot.translator.translate = Mock(side_effect=TranslationUnavailable('offline'))
        with self.assertLogs('translator_bot', level='ERROR'):
            await self.bot.on_message(self.message)
        self.assertIn('Перевод недоступен', self.hook.edit_message.call_args.kwargs['content'])

    async def test_failed_multitarget_translation_uses_original_language(self):
        await self.bot.translation_failed(self.message, ('ro', 'en,ru'))
        self.assertIn('Traducerea nu este disponibilă', self.hook.send.call_args.args[0])

    async def test_pending_placeholder_can_be_flagged(self):
        self.bot.store.link_translation(80, 30, 20, 70, 'en', 'ru', status='pending')
        self.channel.fetch_message = AsyncMock(side_effect=[SimpleNamespace(id=80, webhook_id=70, content='Translating…'),self.message])
        self.bot.get_channel = lambda _: self.channel
        await self.bot.on_raw_reaction_add(SimpleNamespace(
            emoji='🚩', guild_id=1, channel_id=20, message_id=80, user_id=50))
        self.assertEqual(self.bot.store.open_feedback_count(1), 1)

    async def test_failed_translation_notice_can_be_flagged_for_review(self):
        self.bot.store.link_translation(80, 30, 20, 70, 'en', 'ru', status='failed')
        self.channel.fetch_message = AsyncMock(side_effect=[
            SimpleNamespace(id=80, webhook_id=70, content='Перевод недоступен.'), self.message])
        self.bot.get_channel = lambda _: self.channel
        await self.bot.on_raw_reaction_add(SimpleNamespace(
            emoji='🚩', guild_id=1, channel_id=20, message_id=80, user_id=50))
        self.assertEqual(self.bot.store.open_feedback_count(1), 1)
        row = self.bot.store._connection.execute('SELECT * FROM translation_feedback').fetchone()
        self.assertEqual(row['source_text'], 'Hello')
        self.assertIn('translation failed', row['note'])
        self.assertEqual(row['translated_text'], 'Перевод недоступен.')

    async def test_no_result_removes_placeholder(self):
        self.bot.translator.translate = lambda *args: None
        await self.bot.on_message(self.message)
        self.hook.delete_message.assert_awaited_once_with(80)
        self.assertIsNone(self.bot.store.get_translation_link(80))

    async def test_burst_shares_one_placeholder_and_one_generation(self):
        self.bot.batch_pause_seconds = 0.04
        sent = asyncio.Event()
        ids = iter([80, 81])
        async def post(*args, **kwargs):
            sent.set()
            return SimpleNamespace(id=next(ids))
        self.hook.send.side_effect = post
        self.hook.edit_message.side_effect = lambda message_id, **kwargs: SimpleNamespace(id=message_id)
        self.bot.translator.translate = Mock(return_value=SimpleNamespace(source='en', target='ru', text='Привет\nКак дела?'))
        second = SimpleNamespace(**vars(self.message))
        second.id, second.content = 31, 'How are you?'
        first_task = asyncio.create_task(self.bot.on_message(self.message))
        await asyncio.wait_for(sent.wait(), timeout=2)
        await self.bot.on_message(second)
        await first_task
        self.assertEqual(self.hook.send.await_count, 2)
        self.hook.delete_message.assert_awaited_once_with(80)
        self.bot.translator.translate.assert_called_once_with('Hello How are you?', 'auto')
        self.assertEqual(self.bot.store.get_source_links(31)[0]['translation_message_id'], 81)
        self.assertEqual(self.bot.store.get_batch_sources(30), [30, 31])

        # Editing the second source after reopening SQLite updates the full group.
        self.bot.store.close()
        self.bot.store = SettingsStore(Path(self.tmp.name) / 'test.db')
        second.content = 'How are things?'
        self.channel.fetch_message = AsyncMock(side_effect=[second, self.message, second])
        self.bot.get_channel = lambda _: self.channel
        await self.bot.on_raw_message_edit(SimpleNamespace(guild_id=1, channel_id=20, message_id=31,
                                                          data={'content': second.content}))
        self.assertEqual(self.bot.translator.translate.call_args.args[0], 'Hello How are things?')
        self.assertEqual(self.hook.send.await_count, 2)

        # Reporting a group stores all source messages, not just the first one.
        self.channel.fetch_message = AsyncMock(side_effect=[
            SimpleNamespace(id=81, webhook_id=70, content='Привет\nКак дела?'), self.message, second])
        await self.bot.on_raw_reaction_add(SimpleNamespace(emoji='🚩', guild_id=1, channel_id=20,
                                                          message_id=81, user_id=50))
        row = self.bot.store._connection.execute('SELECT source_text FROM translation_feedback').fetchone()
        self.assertEqual(row['source_text'], 'Hello\nHow are things?')

    async def test_different_authors_never_share_batch(self):
        self.bot.batch_pause_seconds = 0.04
        sent = asyncio.Event()
        ids = iter([80, 81])
        async def post(*args, **kwargs):
            sent.set()
            return SimpleNamespace(id=next(ids))
        self.hook.send.side_effect = post
        self.hook.edit_message.side_effect = lambda message_id, **kwargs: SimpleNamespace(id=message_id)
        self.bot.translator.translate = Mock(return_value=SimpleNamespace(source='en', target='ru', text='Привет'))
        other = SimpleNamespace(**vars(self.message))
        other.id = 31
        other.author = SimpleNamespace(**vars(self.message.author))
        other.author.id = 41
        first_task = asyncio.create_task(self.bot.on_message(self.message))
        await asyncio.wait_for(sent.wait(), timeout=2)
        await self.bot.on_message(other)
        await first_task
        self.assertEqual(self.hook.send.await_count, 2)
        self.assertEqual(self.bot.translator.translate.call_count, 2)
        self.assertEqual(self.bot.store.get_batch_sources(30), [30])

    async def test_russian_source_uses_english_placeholder(self):
        self.message.content = 'Привет'
        self.bot.translator.translate = Mock(return_value=SimpleNamespace(source='ru', target='en', text='Hello'))
        await self.bot.on_message(self.message)
        self.assertEqual(self.hook.send.call_args.args, ('Translating…',))

    async def test_completed_translation_moves_and_includes_full_burst(self):
        ids = iter([80, 81])
        operations = []
        async def post(content, **kwargs):
            message_id = next(ids)
            operations.append(('send', message_id))
            return SimpleNamespace(id=message_id)
        async def remove(message_id, **kwargs):
            operations.append(('delete', message_id))
        self.hook.send.side_effect = post
        self.hook.delete_message.side_effect = remove
        self.hook.edit_message.side_effect = lambda message_id, **kwargs: SimpleNamespace(id=message_id)
        self.bot.translator.translate = Mock(return_value=SimpleNamespace(source='en', target='ru', text='Привет'))
        await self.bot.on_message(self.message)
        second = SimpleNamespace(**vars(self.message))
        second.id, second.content = 31, 'One more thing'
        await self.bot.on_message(second)
        self.assertEqual(operations, [('send', 80), ('send', 81), ('delete', 80)])
        self.assertEqual(self.bot.translator.translate.call_args.args[0], 'Hello One more thing')
        self.assertEqual(self.bot.store.get_source_links(30)[0]['translation_message_id'], 81)
        self.assertEqual(self.bot.store.get_source_links(31)[0]['translation_message_id'], 81)

    async def test_expired_burst_starts_new_translation_without_deleting_old(self):
        ids = iter([80, 81])
        self.hook.send.side_effect = lambda *args, **kwargs: SimpleNamespace(id=next(ids))
        self.hook.edit_message.side_effect = lambda message_id, **kwargs: SimpleNamespace(id=message_id)
        await self.bot.on_message(self.message)
        batch = self.bot._batches[(1, 20, 40)]
        batch.last_arrival -= self.bot.burst_window_seconds + 1
        second = SimpleNamespace(**vars(self.message))
        second.id, second.content = 31, 'New topic'
        self.bot.translator.translate = Mock(return_value=SimpleNamespace(source='en', target='ru', text='Новая тема'))
        await self.bot.on_message(second)
        self.bot.translator.translate.assert_called_once_with('New topic', 'auto')
        self.hook.delete_message.assert_not_awaited()

    async def test_intervening_author_ends_finished_burst(self):
        ids = iter([80, 81, 82])
        self.hook.send.side_effect = lambda *args, **kwargs: SimpleNamespace(id=next(ids))
        self.hook.edit_message.side_effect = lambda message_id, **kwargs: SimpleNamespace(id=message_id)
        await self.bot.on_message(self.message)
        other = SimpleNamespace(**vars(self.message))
        other.id = 31
        other.author = SimpleNamespace(**vars(self.message.author))
        other.author.id = 41
        await self.bot.on_message(other)
        third = SimpleNamespace(**vars(self.message))
        third.id, third.content = 32, 'Replying to you'
        self.bot.translator.translate = Mock(return_value=SimpleNamespace(source='en', target='ru', text='Отвечаю тебе'))
        await self.bot.on_message(third)
        self.bot.translator.translate.assert_called_once_with('Replying to you', 'auto')
        self.assertEqual(self.hook.send.await_count, 3)
        self.hook.delete_message.assert_not_awaited()

    async def test_message_arriving_during_inference_discards_partial_result(self):
        entered, release = threading.Event(), threading.Event()
        ids = iter([80, 81])
        self.hook.send.side_effect = lambda *args, **kwargs: SimpleNamespace(id=next(ids))
        self.hook.edit_message.side_effect = lambda message_id, **kwargs: SimpleNamespace(id=message_id)
        def translate(text, mode):
            if text == 'Hello':
                entered.set()
                if not release.wait(timeout=3):
                    raise RuntimeError('Test synchronization failed')
                return SimpleNamespace(source='en', target='ru', text='Устаревший перевод')
            return SimpleNamespace(source='en', target='ru', text='Полный перевод')
        self.bot.translator.translate = Mock(side_effect=translate)
        first_task = asyncio.create_task(self.bot.on_message(self.message))
        self.assertTrue(await asyncio.to_thread(entered.wait, 2))
        second = SimpleNamespace(**vars(self.message))
        second.id, second.content = 31, 'Another line'
        second_task = asyncio.create_task(self.bot.on_message(second))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first_task, second_task)
        self.assertEqual(self.bot.translator.translate.call_count, 2)
        self.assertEqual(self.hook.send.call_args.args, ('Полный перевод',))
        self.assertFalse(any(call.kwargs.get('content') == 'Устаревший перевод'
                             for call in self.hook.edit_message.call_args_list))
        self.hook.delete_message.assert_awaited_once_with(80)

    async def test_failed_replacement_keeps_existing_translation(self):
        await self.bot.on_message(self.message)
        self.hook.send.side_effect = RuntimeError('Webhook unavailable')
        second = SimpleNamespace(**vars(self.message))
        second.id, second.content = 31, 'More text'
        with self.assertLogs('translator_bot', level='ERROR'):
            await self.bot.on_message(second)
        self.hook.delete_message.assert_not_awaited()
        self.assertEqual(self.bot.store.get_source_links(30)[0]['translation_message_id'], 80)

    async def test_split_sentence_becomes_continuous_text_without_losing_real_newlines(self):
        messages = []
        for index, text in enumerate(('Do not cancel', 'the order until', 'the supplier confirms.\nThen let me know.')):
            message = SimpleNamespace(**vars(self.message))
            message.id, message.content = 30 + index, text
            messages.append(message)
        self.assertEqual(combined_message(messages).content,
                         'Do not cancel the order until the supplier confirms.\nThen let me know.')
        self.assertEqual(combined_message(messages, separator='\n').content,
                         'Do not cancel\nthe order until\nthe supplier confirms.\nThen let me know.')

    async def test_complete_grouped_messages_keep_line_boundaries(self):
        messages = []
        for index, text in enumerate(('Ты мне нравишься.))', 'Пойдет?', 'Не правильно. Момент.', 'Ты мне нравишься.)')):
            message = SimpleNamespace(**vars(self.message))
            message.id, message.content = 30 + index, text
            messages.append(message)
        self.assertEqual(combined_message(messages).content,
                         'Ты мне нравишься.))\nПойдет?\nНе правильно. Момент.\nТы мне нравишься.)')

    async def test_existing_message_boundary_newlines_are_not_duplicated(self):
        for before, after in (('Hello.\n', 'Next.'), ('Hello.', '\nNext.')):
            messages = [SimpleNamespace(**vars(self.message)), SimpleNamespace(**vars(self.message))]
            messages[0].content, messages[1].content = before, after
            self.assertEqual(combined_message(messages).content, 'Hello.\nNext.')
