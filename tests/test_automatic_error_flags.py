import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
from translator_bot.bot import TranslatorBot
from translator_bot.config import Settings
from translator_bot.translation import TranslationResult


class AutomaticErrorFlagTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.bot=TranslatorBot(Settings('test',1,Path(self.tmp.name)/'db','INFO','http://localhost:11434','test',90))
        self.bot._connection.user=SimpleNamespace(id=99)
        self.post=SimpleNamespace(id=80,webhook_id=70,content='error',add_reaction=AsyncMock(),remove_reaction=AsyncMock())
        self.channel=SimpleNamespace(id=20,fetch_message=AsyncMock(return_value=self.post))
        self.source=SimpleNamespace(id=30,channel=self.channel,guild=SimpleNamespace(id=1),content='Hello',
            author=SimpleNamespace(id=40,display_name='Name',display_avatar=SimpleNamespace(url='avatar')))
        hook=SimpleNamespace(id=70,send=AsyncMock(return_value=self.post),edit_message=AsyncMock(return_value=self.post),delete_message=AsyncMock())
        self.bot.translation_webhook=AsyncMock(return_value=hook)
        self.bot.get_channel=lambda _:self.channel

    async def asyncTearDown(self):
        await self.bot.close();self.tmp.cleanup()

    async def test_error_self_flags_once_then_success_resolves_only_bot_report(self):
        for _ in range(2):
            await self.bot.publish_translation(self.source,TranslationResult('en','ru','error'),status='failed')
        self.assertEqual(self.bot.store.open_feedback_count(1),1)
        self.post.add_reaction.assert_awaited_with('🚩')
        self.bot.store.add_feedback(guild_id=1,channel_id=20,source_message_id=30,translation_message_id=80,
            source_author_id=40,reporter_id=50,source_language='en',target_language='ru',source_text='Hello',
            translated_text='error',corrected_text=None,note='Human review')
        await self.bot.publish_translation(self.source,TranslationResult('en','ru','pending'),status='pending')
        self.assertEqual(self.bot.store.open_feedback_count(1),2)
        self.post.content='Привет'
        await self.bot.publish_translation(self.source,TranslationResult('en','ru','Привет'))
        self.assertEqual(self.bot.store.open_feedback_count(1),1)
        await self.bot.retry_failures()
        self.post.remove_reaction.assert_awaited_once()
        self.assertEqual(self.post.remove_reaction.call_args.args[1].id,99)
        self.assertEqual(self.bot.store.translation_snapshot(30)['translated_text'],'Привет')

    async def test_permission_failure_does_not_lose_report_or_retry(self):
        self.post.add_reaction.side_effect=discord.Forbidden(SimpleNamespace(status=403,reason='Forbidden'),'Missing permissions')
        await self.bot.translation_failed(self.source,('en','ru'))
        self.assertEqual(self.bot.store.open_feedback_count(1),1)
        self.assertTrue(self.bot.store.due_retries(1e30))

    def test_primary_guild_has_priority_not_every_server(self):
        self.assertLess(self.bot.gpu_priority(1),self.bot.gpu_priority(2))
