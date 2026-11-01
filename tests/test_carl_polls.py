import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from translator_bot.bot import TranslatorBot
from translator_bot.carl_polls import CARL_ID, parse_carl_poll
from translator_bot.config import Settings
from translator_bot.storage import SettingsStore
from translator_bot.translation import TranslationResult
from translator_bot.translation import LocalTranslator

CONTENT='**example** asks: Open the server to the alliance? \n\n1⃣: Yes \n2⃣:  No \n3⃣:  I do not know.'


def source():
    return SimpleNamespace(id=30,content=CONTENT,webhook_id=CARL_ID,guild=SimpleNamespace(id=1),
        author=SimpleNamespace(id=CARL_ID,bot=True,display_name='Carl-bot',display_avatar=SimpleNamespace(url='https://example.com/avatar')),
        add_reaction=AsyncMock(),remove_reaction=AsyncMock())


class PollFormatTests(unittest.TestCase):
    def test_exact_known_carl_poll_extracts_only_question_and_options(self):
        poll=parse_carl_poll(source())
        self.assertEqual(poll.text,'Open the server to the alliance?\nYes\nNo\nI do not know.')
        rendered=poll.render('Открыть сервер для альянса?\nДа\nНет\nНе знаю.','ru','https://discord.com/channels/1/20/30')
        self.assertIn('1⃣: Да\n2⃣: Нет\n3⃣: Не знаю.',rendered)
        self.assertIn('[Голосуйте в оригинальном опросе](https://discord.com/channels/1/20/30)',rendered)

    def test_other_bots_users_webhooks_and_malformed_options_are_rejected(self):
        for changes in ({'author':SimpleNamespace(id=7,bot=True)},
                        {'author':SimpleNamespace(id=CARL_ID,bot=False)}, {'webhook_id':7},
                        {'content':CONTENT.replace('2⃣:','1⃣:')},
                        {'content':CONTENT.replace('3⃣:','4⃣:')}, {'content':'Carl says hello.'}):
            message=source()
            for key,value in changes.items():setattr(message,key,value)
            self.assertIsNone(parse_carl_poll(message))

    def test_bad_line_alignment_is_never_rendered_as_a_poll(self):
        with self.assertRaises(ValueError):
            parse_carl_poll(source()).render('Открыть? Да Нет Не знаю.','ru','url')

    def test_server_release_access_idiom_is_narrowly_guided(self):
        t=LocalTranslator('http://localhost')
        self.assertIn('open access',t._prompt('Do we release the server to the alliance?','en','ru'))
        self.assertNotIn('open access',t._prompt('Release the parking brake.','en','ru'))


class CarlIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'test.db'
        self.bot=TranslatorBot(Settings('test',1,self.path,'INFO','http://localhost','test',90))
        self.bot._connection.user=SimpleNamespace(id=99)
        self.source=source()
        self.post=SimpleNamespace(id=80,webhook_id=70,content='',add_reaction=AsyncMock(),remove_reaction=AsyncMock())
        self.channel=SimpleNamespace(id=20,fetch_message=AsyncMock(side_effect=lambda id:self.source if id==30 else self.post))
        self.source.channel=self.channel
        self.bot.get_channel=lambda _:self.channel
        async def edit(id,**kwargs):
            self.post.content=kwargs['content']
            return self.post
        self.hook=SimpleNamespace(id=70,send=AsyncMock(return_value=self.post),
            edit_message=AsyncMock(side_effect=edit),delete_message=AsyncMock())
        self.bot.translation_webhook=AsyncMock(return_value=self.hook)
        self.bot.translator._translate_uncached=Mock(return_value=TranslationResult('en','ru',
            'Открыть сервер для альянса?\nДа\nНет\nНе знаю.'))

    async def asyncTearDown(self):
        await self.bot.close()
        self.temp.cleanup()

    async def test_poll_is_translated_without_grouping_and_duplicate_events_are_noops(self):
        await self.bot.on_message(self.source)
        self.assertIn('1⃣: Да',self.post.content)
        self.assertIn('/1/20/30)',self.post.content)
        self.bot.translator._translate_uncached.assert_called_once()
        await self.bot.on_message(self.source)
        self.assertEqual(self.hook.send.await_count,1)
        self.assertEqual(self.hook.edit_message.await_count,1)
        self.assertFalse(self.bot._batches)
        self.bot.store.close();self.bot.store=SettingsStore(self.path)
        await self.bot.on_message(self.source)
        self.assertEqual(self.hook.edit_message.await_count,1)

    async def test_concurrent_create_and_report_do_not_duplicate_companion(self):
        await asyncio.gather(self.bot.process_carl_poll(self.source),self.bot.process_carl_poll(self.source))
        self.assertEqual(self.hook.send.await_count,1)
        self.bot.translator._translate_uncached.assert_called_once()

    async def test_reactions_on_companion_never_vote_or_mirror_to_original(self):
        await self.bot.on_message(self.source)
        for emoji in ('1⃣','2⃣','👍','❤️'):
            payload=SimpleNamespace(guild_id=1,channel_id=20,message_id=80,user_id=50,emoji=emoji)
            await self.bot.sync_reaction(payload,added=True)
        self.source.add_reaction.assert_not_awaited()
        self.assertEqual(self.bot.store._connection.execute('SELECT COUNT(*) FROM mirrored_reactions').fetchone()[0],0)

    async def test_text_edits_update_same_companion_but_vote_only_updates_do_not_translate(self):
        await self.bot.on_message(self.source)
        self.source.content=CONTENT.replace('Open the server','Invite members')
        await self.bot.on_raw_message_edit(SimpleNamespace(guild_id=1,channel_id=20,message_id=30,data={'content':self.source.content}))
        self.assertEqual(self.hook.send.await_count,1)
        self.assertEqual(self.hook.edit_message.await_count,2)
        before=self.bot.translator._translate_uncached.call_count
        await self.bot.on_raw_message_edit(SimpleNamespace(guild_id=1,channel_id=20,message_id=30,data={'embeds':[]}))
        self.assertEqual(self.bot.translator._translate_uncached.call_count,before)

    async def test_disabled_channel_and_untrusted_bot_are_not_translated(self):
        self.bot.store.set_channel_enabled(1,20,False)
        await self.bot.on_message(self.source)
        self.hook.send.assert_not_awaited()
        self.bot.store.set_channel_enabled(1,20,True)
        self.source.author.id=7
        await self.bot.on_message(self.source)
        self.hook.send.assert_not_awaited()

    async def test_deleting_poll_removes_companion(self):
        await self.bot.on_message(self.source)
        await self.bot.on_raw_message_delete(SimpleNamespace(guild_id=1,channel_id=20,message_id=30))
        self.hook.delete_message.assert_awaited_once_with(80)
        self.assertEqual(self.bot.store.get_source_links(30),[])

    async def test_flag_on_original_carl_poll_is_reportable(self):
        await self.bot.on_message(self.source)
        await self.bot.on_raw_reaction_add(SimpleNamespace(guild_id=1,channel_id=20,message_id=30,
            user_id=50,emoji='🚩'))
        self.assertEqual(self.bot.store.open_feedback_count(1),1)
        self.assertEqual(self.hook.send.await_count,1)

    async def test_country_flag_translation_preserves_poll_rendering_and_vote_link(self):
        self.hook.send.return_value=SimpleNamespace(id=81)
        delivered=await self.bot.deliver_flag_translation(self.source,[30],50,'ru')
        self.assertTrue(delivered)
        self.assertIn('1⃣: Да',self.post.content)
        self.assertIn('Голосуйте в оригинальном опросе',self.post.content)
        self.source.add_reaction.assert_not_awaited()
