"""Recover explicitly selected existing report reactions via the normal handler.

Usage: replay_visible_flags.py GUILD_ID CHANNEL_ID MESSAGE_ID,MESSAGE_ID
Only selected human 🚩 reactions are processed. No history-wide backfill.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8')
from translator_bot.bot import TranslatorBot
from translator_bot.config import Settings

class ReplayBot(TranslatorBot):
    async def setup_hook(self):
        pass  # REST-only: do not sync commands, connect Gateway, or start loops.

async def main():
    guild_id, channel_id = map(int, sys.argv[1:3])
    settings = Settings.load()
    if guild_id not in settings.guild_ids:
        raise RuntimeError('Guild outside allowed servers.')
    async with ReplayBot(settings) as bot:
        await bot.login(settings.discord_token)
        guild = await bot.fetch_guild(guild_id)
        channel = await guild.fetch_channel(channel_id)
        bot.get_channel = lambda id: channel if id == channel_id else None
        for id in map(int, sys.argv[3].split(',')):
            message = await channel.fetch_message(id)
            for reaction in message.reactions:
                if str(reaction.emoji) != '🚩':
                    continue
                async for reporter in reaction.users():
                    if reporter.bot:
                        continue
                    await bot.on_raw_reaction_add(SimpleNamespace(emoji='🚩',guild_id=guild_id,
                        channel_id=channel_id,message_id=id,user_id=reporter.id))
            print('PROCESSED VISIBLE FLAGS', id, flush=True)
        print('REPORTS', [dict(r) for r in bot.store._connection.execute(
            'SELECT id,source_message_id,translation_message_id,source_text FROM translation_feedback WHERE id>43')])

asyncio.run(main())
