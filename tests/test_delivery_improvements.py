import asyncio
import tempfile
import unittest
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import discord
from translator_bot.bot import TranslatorBot, PublishOutcome
from translator_bot.config import Settings
from translator_bot.translation import TranslationResult


class DeliveryImprovementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bot = TranslatorBot(Settings('test',1,Path(self.temp.name)/'db','INFO','http://localhost','test',90))
        self.bot.batch_pause_seconds = 0
        self.source = SimpleNamespace(id=30,content='Hello',guild=SimpleNamespace(id=1),
            webhook_id=None,author=SimpleNamespace(id=40,bot=False,display_name='Alice',
                display_avatar=SimpleNamespace(url='https://example.com/avatar')))
        self.post = SimpleNamespace(id=80,webhook_id=70,content='Привет')
        self.channel = SimpleNamespace(id=20,fetch_message=AsyncMock(
            side_effect=lambda id: self.source if id==30 else self.post))
        self.source.channel = self.channel
        self.hook = SimpleNamespace(id=70,channel_id=20,send=AsyncMock(return_value=self.post),
            edit_message=AsyncMock(return_value=self.post),delete_message=AsyncMock())
        self.bot.get_channel = lambda _: self.channel
        self.bot.translation_webhook = AsyncMock(return_value=self.hook)
        self.bot.translator.translate = Mock(return_value=TranslationResult('en','ru','Привет'))

    async def asyncTearDown(self):
        await self.bot.close()
        self.temp.cleanup()

    def failed_retry(self):
        self.bot.store.link_translation(80,30,20,70,'en','ru',status='failed')
        self.bot.store.schedule_retry(30,1,20,0)

    async def test_missing_destination_never_logs_success_or_keeps_retrying(self):
        self.failed_retry()
        self.hook.edit_message.side_effect = discord.NotFound(SimpleNamespace(status=404,reason='Not found'),'gone')
        with self.assertLogs('translator_bot',level='INFO') as logs:
            await self.bot.retry_failures()
        self.assertFalse(any('retry succeeded' in line for line in logs.output))
        self.assertTrue(any('permanently missing' in line for line in logs.output))
        self.assertEqual(self.bot.store.due_retries(1e30), [])

    async def test_unavailable_original_webhook_is_explicit_missing(self):
        self.failed_retry()
        self.hook.id = 71
        outcome = await self.bot.publish_translation(self.source,TranslationResult('en','ru','Привет'))
        self.assertIs(outcome, PublishOutcome.MISSING)
        self.assertEqual(self.bot.store.due_retries(1e30), [])
        self.hook.send.assert_not_awaited()

    async def test_transient_delivery_error_keeps_retry_and_never_logs_success(self):
        self.failed_retry()
        self.hook.edit_message.side_effect = discord.HTTPException(SimpleNamespace(status=503,reason='Unavailable'),'retry')
        with self.assertLogs('translator_bot',level='INFO') as logs:
            await self.bot.retry_failures()
        self.assertFalse(any('retry succeeded' in line for line in logs.output))
        self.assertEqual(self.bot.store.due_retries(1e30)[0]['attempts'],1)

    async def test_retry_publishes_after_releasing_gpu(self):
        self.failed_retry()
        async def edit(*args, **kwargs):
            self.assertFalse(self.bot._translation_slots.locked())
            return self.post
        self.hook.edit_message.side_effect = edit
        with self.assertLogs('translator_bot',level='INFO') as logs:
            await self.bot.retry_failures()
        self.assertTrue(any('retry succeeded' in line for line in logs.output))

    async def test_slow_delivery_does_not_hold_gpu_and_logs_total_latency(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def edit(*args, **kwargs):
            self.assertFalse(self.bot._translation_slots.locked())
            entered.set()
            await release.wait()
            return self.post
        self.hook.edit_message.side_effect = edit
        with self.assertLogs('translator_bot',level='INFO') as logs:
            task = asyncio.create_task(self.bot.on_message(self.source))
            try:
                await asyncio.wait_for(entered.wait(),2)
                self.bot.translator.translate_to = Mock(return_value=TranslationResult('en','ru','Второй'))
                other = await asyncio.wait_for(self.bot.translate_on_demand('Second message','ru','en'),2)
                self.assertEqual(other.text,'Второй')
            finally:
                release.set()
                await task
        self.assertTrue(any('Delivery metrics' in line and 'outcome=delivered' in line for line in logs.output))
        self.assertTrue(any('Discord delivery' in line and 'seconds=' in line for line in logs.output))
        self.assertFalse(any('Second message' in line for line in logs.output))

    async def test_periodic_cache_metrics_do_not_expose_source_text(self):
        self.bot._last_cache_metrics = time.monotonic()-301
        with self.assertLogs('translator_bot', level='INFO') as logs:
            await self.bot.retry_failures()
        self.assertTrue(any('lookup_hit_percent' in line for line in logs.output))
        self.assertFalse(any('Hello' in line for line in logs.output))

    async def test_partial_multichunk_delivery_keeps_retry_until_all_chunks_complete(self):
        self.failed_retry()
        self.bot.translator.translate.return_value = TranslationResult('en','ru','А'*2000)
        self.hook.send.side_effect = discord.HTTPException(SimpleNamespace(status=503,reason='Unavailable'),'retry')
        await self.bot.retry_failures()
        self.assertEqual(self.bot.store.get_source_links(30)[0]['status'], 'pending')
        self.assertEqual(self.bot.store.due_retries(1e30)[0]['attempts'], 1)
        self.hook.send.side_effect = None
        self.hook.send.return_value = SimpleNamespace(id=81)
        with self.bot.store._connection:
            self.bot.store._connection.execute('UPDATE translation_retries SET next_at=0')
        await self.bot.retry_failures()
        self.assertTrue(all(row['status']=='complete' for row in self.bot.store.get_source_links(30)))
        self.assertEqual(self.bot.store.due_retries(1e30), [])
