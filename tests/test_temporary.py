import asyncio
import threading
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch

from translator_bot.bot import TranslatorBot
from translator_bot.languages import SUBDIVISION_FLAGS
from translator_bot.config import Settings
from translator_bot.storage import SettingsStore
from translator_bot.translation import TranslationResult, TranslationUnavailable


class TemporaryDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bot = TranslatorBot(Settings('test', 1, Path(self.tmp.name) / 'test.db', 'INFO',
                                          'http://localhost:11434', 'test', 90))
        self.hook = SimpleNamespace(id=70, send=AsyncMock(return_value=SimpleNamespace(id=80)),
                                   edit_message=AsyncMock(), delete_message=AsyncMock())
        self.bot.translation_webhook = AsyncMock(return_value=self.hook)
        self.bot.on_demand_retry_delays = (0, 0, 0)
        self.channel = SimpleNamespace(id=20)
        self.message = SimpleNamespace(id=30, guild=SimpleNamespace(id=1), channel=self.channel,
            content='This is a test message for translation.', webhook_id=None,
            remove_reaction=AsyncMock(), author=SimpleNamespace(id=40, bot=False,
                display_name='Alice', display_avatar=SimpleNamespace(url='https://example.com/avatar.png')))
        self.channel.fetch_message = AsyncMock(return_value=self.message)
        self.bot.get_channel = lambda _: self.channel
        self.bot.translator.translate_to = Mock(return_value=TranslationResult('en', 'ro', 'Acesta este un mesaj de test.'))
        self.payload = SimpleNamespace(guild_id=1, channel_id=20, message_id=30, user_id=50, emoji='🇷🇴')

    async def asyncTearDown(self):
        await self.bot.close()
        self.tmp.cleanup()

    async def test_flag_translation_is_public_timed_and_reaction_removed_after_delivery(self):
        started = time.time()
        await self.bot.on_raw_reaction_add(self.payload)
        row = self.bot.store.temporary_posts()[0]
        self.assertGreaterEqual(row['expires_at'], int(started) + 90)
        self.assertEqual(self.hook.send.call_args.args[0].split('\n')[-1], 'Traduc…')
        self.assertIn('Acesta este un mesaj de test.', self.hook.edit_message.call_args.kwargs['content'])
        self.assertIn('deletes <t:', self.hook.edit_message.call_args.kwargs['content'])
        self.message.remove_reaction.assert_awaited_once()
        self.assertEqual(self.message.remove_reaction.call_args.args[1].id, 50)
        self.assertEqual(self.bot.store.get_source_links(30), [])
        self.assertFalse(self.hook.send.call_args.kwargs['allowed_mentions'].everyone)

    async def test_england_reaction_routes_to_english(self):
        self.payload.emoji = SUBDIVISION_FLAGS['England']
        self.bot.request_flag_translation = AsyncMock()
        await self.bot.on_raw_reaction_add(self.payload)
        self.bot.request_flag_translation.assert_awaited_once_with(self.payload, 'en')

    async def test_unsupported_flags_do_not_trigger_translation(self):
        self.bot.request_flag_translation = AsyncMock()
        for emoji in ('🏴', '🇮🇳', '🇪🇺', '<:flag_gb:123>'):
            self.payload.emoji = emoji
            await self.bot.on_raw_reaction_add(self.payload)
        self.bot.request_flag_translation.assert_not_awaited()

    async def test_expiry_deletes_after_restart_without_touching_automatic_translations(self):
        self.bot.store.link_translation(81, 30, 20, 70, 'en', 'ru')
        self.bot.store.add_temporary(80, 1, 20, 70, 'en', 'ro', time.time() - 1, [30])
        self.bot.store.close()
        self.bot.store = SettingsStore(Path(self.tmp.name) / 'test.db')
        await self.bot.expire_temporary()
        self.hook.delete_message.assert_awaited_once_with(80)
        self.assertEqual(self.bot.store.temporary_posts(), [])
        self.assertIsNotNone(self.bot.store.get_translation_link(81))

    async def test_original_deletion_removes_requested_translation(self):
        await self.bot.on_raw_reaction_add(self.payload)
        await self.bot.on_raw_message_delete(SimpleNamespace(guild_id=1, channel_id=20, message_id=30))
        self.hook.delete_message.assert_awaited_once_with(80)
        self.assertEqual(self.bot.store.temporary_posts(), [])

    async def test_failed_translation_does_not_remove_requesters_flag(self):
        self.bot.translator.translate_to.side_effect = TranslationUnavailable('offline')
        with self.assertLogs('translator_bot', level='ERROR'):
            await self.bot.on_raw_reaction_add(self.payload)
        self.message.remove_reaction.assert_not_awaited()
        self.hook.delete_message.assert_awaited_once_with(80)
        self.assertEqual(self.bot.store.temporary_posts(), [])

    async def test_reading_timer_starts_after_queue_wait(self):
        self.bot.store.add_temporary(80, 1, 20, 70, 'en', 'ro', 0, [30])
        with patch('translator_bot.bot.time.time', side_effect=[1000, 2000]):
            await self.bot.deliver_flag_translation(self.message, [30], 50, 'ro')
        self.assertEqual(self.bot.store.temporary_posts()[0]['expires_at'], 2090)

    async def test_no_text_never_posts_placeholder(self):
        self.message.content = '😀'
        await self.bot.on_raw_reaction_add(self.payload)
        self.hook.send.assert_not_awaited()
        self.bot.translator.translate_to.assert_not_called()

    async def test_original_deletion_during_requested_inference_never_resurrects_text(self):
        entered, release = threading.Event(), threading.Event()
        def translate(*args):
            entered.set()
            release.wait(timeout=3)
            return TranslationResult('en', 'ro', 'Mesaj șters')
        self.bot.translator.translate_to.side_effect = translate
        task = asyncio.create_task(self.bot.on_raw_reaction_add(self.payload))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            await self.bot.on_raw_message_delete(SimpleNamespace(guild_id=1, channel_id=20, message_id=30))
        finally:
            release.set()
            await task
        self.hook.edit_message.assert_not_awaited()
        self.message.remove_reaction.assert_not_awaited()
        self.assertEqual(self.bot.store.temporary_posts(), [])

    async def test_concurrent_same_language_requests_share_one_post(self):
        entered, release = threading.Event(), threading.Event()
        def translate(*args):
            entered.set()
            release.wait(timeout=3)
            return TranslationResult('en', 'ro', 'Mesaj tradus')
        self.bot.translator.translate_to.side_effect = translate
        task = asyncio.create_task(self.bot.on_raw_reaction_add(self.payload))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            other = SimpleNamespace(**vars(self.payload))
            other.user_id = 51
            second = asyncio.create_task(self.bot.on_raw_reaction_add(other))
            await asyncio.sleep(0)
        finally:
            release.set()
            await task
            await second
        self.hook.send.assert_awaited_once()
        self.bot.translator.translate_to.assert_called_once()
        self.assertEqual(self.message.remove_reaction.await_count, 2)
