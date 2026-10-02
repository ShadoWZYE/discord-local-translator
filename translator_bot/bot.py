from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .config import Settings
from .storage import SettingsStore, TranslationMode
from .translation import LocalTranslator, TranslationResult, TranslationUnavailable, direction_for
from .languages import LANGUAGES, FLAG_LANGUAGES, DEFAULT_LANGUAGES, TRANSLATION_ERRORS, SUBDIVISION_FLAGS, parse_languages, reading_seconds


LOGGER = logging.getLogger("translator_bot")
MODE_LABELS = {
    "auto": "Auto-detect English/Russian",
    "en_to_ru": "Always English → Russian",
    "ru_to_en": "Always Russian → English",
    "off": "Do not translate my messages",
}
FLAG = {"en": "🇬🇧", "ru": "🇷🇺"}
CORRECTED_REACTION = '🔄'
TRANSLATING = {
    'en': 'Translating…', 'ru': 'Перевожу…', 'ro': 'Traduc…', 'fr': 'Traduction…',
    'de': 'Übersetze…', 'es': 'Traduciendo…', 'it': 'Traduzione…', 'pt': 'Traduzindo…',
    'nl': 'Vertalen…', 'pl': 'Tłumaczę…', 'uk': 'Перекладаю…', 'tr': 'Çeviriyorum…',
    'cs': 'Překládám…', 'el': 'Μετάφραση…', 'sv': 'Översätter…', 'fi': 'Käännän…',
    'hu': 'Fordítás…', 'bg': 'Превеждам…', 'ar': 'جارٍ الترجمة…', 'he': 'מתרגם…',
    'ja': '翻訳中…', 'ko': '번역 중…', 'zh': '正在翻译…',
}


@dataclass
class MessageBatch:
    messages: list[discord.Message]
    mode: TranslationMode
    direction: tuple[str, str]
    started: float
    last_arrival: float
    accepting: bool = True
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    closed: bool = False
    version: int = 0
    needs_move: bool = False
    expiry: asyncio.TimerHandle | None = None


def combined_message(messages: list[discord.Message], *, separator: str | None = None):
    first = messages[0]
    contents = [message.content for message in messages]
    if separator is not None:
        content = separator.join(contents)
    else:
        content = contents[0]
        for previous, following in zip(contents, contents[1:]):
            # Preserve distinct completed messages without breaking a sentence
            # deliberately sent as several fragments. All lines still reach the
            # model together, so pronouns and continuations retain full context.
            complete = re.search(r'''[.!?…][)\]"'»”’*~ \t]*$|\)+[ \t]*$''', previous.rstrip())
            boundary = '' if previous.endswith('\n') or following.startswith('\n') else '\n' if complete else ' '
            content += boundary + following
    return SimpleNamespace(id=first.id, guild=first.guild, channel=first.channel,
                           author=first.author, webhook_id=None,
                           content=content)


def split_for_discord(text: str, limit: int = 1_900) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        cut = min(limit, len(remaining))
        if cut < len(remaining):
            newline = remaining.rfind("\n", 0, cut)
            space = remaining.rfind(" ", 0, cut)
            cut = max(newline, space, limit // 2)
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks


class TranslatorBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        self.guild_ids = frozenset(settings.guild_ids)
        self.store = SettingsStore(settings.database_path, {
            guild_id: guild_id not in settings.default_off_guild_ids for guild_id in self.guild_ids
        })
        self.translator = LocalTranslator(
            base_url=settings.ollama_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
        )
        # One GPU generation at a time gives lower and more predictable latency.
        self._translation_slots = asyncio.Semaphore(1)
        self._webhooks: dict[int, discord.Webhook] = {}
        self._webhook_lock = asyncio.Lock()
        self._batch_lock = asyncio.Lock()
        self._batches: dict[tuple[int, int, int], MessageBatch] = {}
        self._batch_tasks: set[asyncio.Task] = set()
        self._working_batches: dict[asyncio.Task, MessageBatch] = {}
        self.batch_pause_seconds = 1.5
        self.batch_max_seconds = 5.0
        self.burst_window_seconds = settings.burst_window_seconds
        self._flag_requests: dict[tuple, asyncio.Task] = {}
        self._reaction_lock = asyncio.Lock()
        self.on_demand_retry_delays = (5, 15, 45)

    async def translate_on_demand(self, text, target, source=None):
        for attempt in range(len(self.on_demand_retry_delays)+1):
            try:
                async with self._translation_slots:
                    return await asyncio.to_thread(self.translator.translate_to, text, target, source)
            except TranslationUnavailable:
                if attempt == len(self.on_demand_retry_delays):
                    raise
                LOGGER.warning('Retrying failed on-demand translation (%d/3)', attempt+1)
                # Release the GPU slot while waiting; normal chat can proceed.
                await asyncio.sleep(self.on_demand_retry_delays[attempt])

    def translate_content(self, content, mode, guild_id, channel_id):
        languages = self.store.get_channel_languages(guild_id, channel_id)
        if languages == DEFAULT_LANGUAGES or mode != 'auto':
            return self.translator.translate(content, mode)
        return self.translator.translate_languages(content, mode, languages)

    def start_batch_task(self, key, batch: MessageBatch) -> None:
        batch.task = asyncio.create_task(self.translate_batch(key, batch))
        self._batch_tasks.add(batch.task)
        batch.task.add_done_callback(self._batch_tasks.discard)
        self._working_batches[batch.task] = batch
        batch.task.add_done_callback(lambda task: self._working_batches.pop(task, None))

    def renew_batch_expiry(self, key, batch: MessageBatch) -> None:
        if batch.expiry:
            batch.expiry.cancel()
        batch.expiry = asyncio.get_running_loop().call_later(
            self.burst_window_seconds, self.expire_batch, key, batch
        )

    def expire_batch(self, key, batch: MessageBatch) -> None:
        if self._batches.get(key) is not batch:
            return
        if batch.task and not batch.task.done():
            batch.expiry = asyncio.get_running_loop().call_later(1, self.expire_batch, key, batch)
        else:
            self._batches.pop(key, None)

    async def translation_webhook(self, channel: discord.TextChannel | discord.Thread) -> discord.Webhook:
        parent = channel.parent if isinstance(channel, discord.Thread) else channel
        if parent is None:
            raise RuntimeError("Thread parent is unavailable")
        async with self._webhook_lock:
            if parent.id not in self._webhooks:
                hooks = await parent.webhooks()
                hook = next((h for h in hooks if h.name == "EN-RU Translations"
                             and h.user and self.user and h.user.id == self.user.id), None)
                if hook is None:
                    hook = await parent.create_webhook(name="EN-RU Translations", reason="Automatic translations")
                self._webhooks[parent.id] = hook
            return self._webhooks[parent.id]

    async def setup_hook(self) -> None:
        try:
            await asyncio.to_thread(self.translator.warmup)
            LOGGER.info("Local translation model %s is warm and verified", self.settings.ollama_model)
        except TranslationUnavailable:
            # Fail closed: connect so commands/status remain visible, but never send a
            # lower-quality or remote fallback translation.
            LOGGER.exception("Local translation model warmup failed; automatic replies will fail closed")
        for guild_id in self.settings.guild_ids:
            await self.sync_server(guild_id)

    async def sync_server(self, guild_id: int) -> None:
        guild = discord.Object(id=guild_id)
        self.tree.copy_global_to(guild=guild)
        try:
            synced = await self.tree.sync(guild=guild)
            LOGGER.info("Synchronized %d slash commands to guild %d", len(synced), guild_id)
        except discord.Forbidden:
            LOGGER.warning("Cannot sync server %d yet; install the bot there first", guild_id)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if guild.id in self.guild_ids:
            await self.sync_server(guild.id)

    async def close(self) -> None:
        retry_task = self.retry_failures.get_task()
        self.retry_failures.cancel()
        if retry_task:
            await asyncio.gather(retry_task, return_exceptions=True)
        expiry_task = self.expire_temporary.get_task()
        self.expire_temporary.cancel()
        flag_tasks = list(self._flag_requests.values())
        for task in flag_tasks:
            task.cancel()
        if flag_tasks or expiry_task:
            await asyncio.gather(*flag_tasks, *([expiry_task] if expiry_task else []), return_exceptions=True)
        for batch in self._batches.values():
            if batch.expiry:
                batch.expiry.cancel()
        tasks = list(self._batch_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.store.close()
        await super().close()

    async def on_ready(self) -> None:
        LOGGER.info("Ready as %s (%s)", self.user, self.user.id if self.user else "unknown")
        if not self.expire_temporary.is_running():
            self.expire_temporary.start()
        for link in self.store.unfinished_links():
            channel = self.get_channel(link['channel_id'])
            if channel and getattr(channel, 'guild', None) and channel.guild.id in self.guild_ids:
                self.store.schedule_retry(link['source_message_id'], channel.guild.id, channel.id, time.time()+5)
        if not self.retry_failures.is_running():
            self.retry_failures.start()

    @tasks.loop(seconds=5)
    async def retry_failures(self) -> None:
        for report in self.store.corrected_flags_due(time.time()):
            self.store.defer_flag_cleanup(report['id'], time.time()+300)
            if report['guild_id'] not in self.guild_ids:
                continue
            channel = self.get_channel(report['channel_id'])
            if not channel:
                continue
            try:
                translated = await channel.fetch_message(report['translation_message_id'])
                link = self.store.get_translation_link(translated.id)
                if (not link or translated.webhook_id != link['webhook_id']
                        or translated.content != report['corrected_text']):
                    # Never label a newer edit or unrelated post as this repair.
                    self.store.defer_flag_cleanup(report['id'], 1e30)
                    continue
                await translated.add_reaction(CORRECTED_REACTION)
                for message_id in self.store.feedback_flag_messages(report['id']):
                    try:
                        flagged = translated if message_id == translated.id else await channel.fetch_message(message_id)
                        await flagged.remove_reaction('🚩', discord.Object(id=report['reporter_id']))
                    except discord.NotFound:
                        pass
                self.store.defer_flag_cleanup(report['id'], 1e30)
            except discord.NotFound:
                self.store.defer_flag_cleanup(report['id'], 1e30)
            except discord.HTTPException:
                LOGGER.warning('Corrected report %s: repair marker/flag cleanup failed; check Add Reactions and Manage Messages; will retry later', report['id'])
        for job in self.store.due_retries(time.time()):
            source_id = job['source_id']
            channel = self.get_channel(job['channel_id'])
            self.store.advance_retry(source_id, time.time() + (15, 45, 120)[job['attempts']])
            if job['guild_id'] not in self.guild_ids or not channel:
                continue
            links = self.store.get_source_links(source_id)
            if (not links or not all(row['status'] in ('failed', 'pending') for row in links)
                    or not self.store.is_channel_enabled(job['guild_id'], channel.id)):
                self.store.cancel_retry(source_id)
                continue
            try:
                async with self._translation_slots:
                    original = await self.fetch_batch(channel, source_id)
                    mode = self.store.get_user_mode(job['guild_id'], original.author.id)
                    if mode == 'off':
                        self.store.cancel_retry(source_id)
                        continue
                    result = await asyncio.to_thread(self.translate_content, original.content, mode, job['guild_id'], channel.id)
                    async with self._batch_lock:
                        current = await self.fetch_batch(channel, source_id)
                        current_links = self.store.get_source_links(source_id)
                        if (current.content != original.content or not current_links
                                or not all(row['status'] in ('failed', 'pending') for row in current_links)
                                or not self.store.is_channel_enabled(job['guild_id'], channel.id)
                                or self.store.get_user_mode(job['guild_id'], current.author.id) != mode):
                            continue
                        await self.publish_translation(current, result)
                        LOGGER.info('Automatic failure retry succeeded for original %s', source_id)
            except discord.NotFound:
                self.store.cancel_retry(source_id)
            except (TranslationUnavailable, discord.HTTPException):
                LOGGER.warning('Automatic failure retry %d/3 did not succeed for original %s', job['attempts']+1, source_id)
            except Exception:
                LOGGER.exception('Automatic failure retry error for original %s', source_id)

    async def delete_temporary(self, post) -> None:
        channel = self.get_channel(post['channel_id'])
        if channel is None:
            try:
                channel = await self.fetch_channel(post['channel_id'])
            except discord.NotFound:
                self.store.remove_temporary(post['message_id'])
                return
        hook = await self.translation_webhook(channel)
        if hook.id != post['webhook_id']:
            LOGGER.warning('Cannot delete timed translation %s: original webhook unavailable', post['message_id'])
            return
        thread_args = {'thread': channel} if isinstance(channel, discord.Thread) else {}
        try:
            await hook.delete_message(post['message_id'], **thread_args)
        except discord.NotFound:
            pass
        self.store.remove_temporary(post['message_id'])

    @tasks.loop(seconds=5)
    async def expire_temporary(self) -> None:
        async with self._batch_lock:
            for post in self.store.temporary_posts(expires_before=time.time()):
                if post['guild_id'] not in self.guild_ids:
                    continue
                try:
                    await self.delete_temporary(post)
                except discord.HTTPException:
                    LOGGER.warning('Could not delete timed translation %s; will retry', post['message_id'])

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.webhook_id or not message.guild:
            return
        if message.guild.id not in self.guild_ids:
            return
        # Another person's message closes the current burst even if it is just emoji.
        for key, batch in list(self._batches.items()):
            if key[:2] == (message.guild.id, message.channel.id) and key[2] != message.author.id:
                batch.accepting = False
                batch.closed = True
                batch.changed.set()
        if not message.content.strip():
            return
        if not self.store.is_channel_enabled(message.guild.id, message.channel.id):
            return

        mode = self.store.get_user_mode(message.guild.id, message.author.id)
        direction = direction_for(message.content, mode, self.store.get_channel_languages(message.guild.id, message.channel.id))
        if direction is None:
            return

        key = (message.guild.id, message.channel.id, message.author.id)
        try:
            async with self._batch_lock:
                now = asyncio.get_running_loop().time()
                batch = self._batches.get(key)
                if (batch and not batch.closed and batch.mode == mode and batch.direction == direction
                    and now - batch.last_arrival < self.burst_window_seconds
                    and sum(len(m.content) + 1 for m in batch.messages) + len(message.content) <= 1900):
                    batch.messages.append(message)
                    batch.last_arrival = now
                    batch.version += 1
                    self.store.add_batch_source(message.id, batch.messages[0].id)
                    if batch.task.done():
                        # A finished translation moves below the newly extended originals.
                        await self.publish_translation(
                            combined_message(batch.messages),
                            TranslationResult(direction[0], direction[1], TRANSLATING.get(direction[1], 'Translating…')),
                            status="pending", replace=True,
                        )
                        batch.started = now
                        batch.accepting = True
                        batch.needs_move = False
                        self.start_batch_task(key, batch)
                    else:
                        batch.needs_move = True
                    batch.changed.set()
                else:
                    if batch:
                        batch.accepting = False
                        batch.closed = True
                        batch.changed.set()
                        if batch.expiry:
                            batch.expiry.cancel()
                    await self.publish_translation(
                        message, TranslationResult(direction[0], direction[1], TRANSLATING.get(direction[1], 'Translating…')), status="pending"
                    )
                    now = asyncio.get_running_loop().time()
                    batch = MessageBatch([message], mode, direction, now, now)
                    self._batches[key] = batch
                    self.start_batch_task(key, batch)
                self.renew_batch_expiry(key, batch)
            await asyncio.shield(batch.task)
        except discord.Forbidden:
            LOGGER.warning("Cannot post webhook in channel %s; check permissions", message.channel.id)
        except Exception:
            LOGGER.exception("Could not queue translation for message %s", message.id)

    async def translate_batch(self, key: tuple[int, int, int], batch: MessageBatch) -> None:
        if not batch.messages:
            return
        message = combined_message(batch.messages)
        try:
            while True:
                while batch.accepting and not batch.closed:
                    delay = min(batch.started + self.batch_max_seconds,
                                batch.last_arrival + self.batch_pause_seconds) - asyncio.get_running_loop().time()
                    if delay <= 0:
                        break
                    batch.changed.clear()
                    try:
                        await asyncio.wait_for(batch.changed.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        break
                batch.accepting = False
                async with self._translation_slots:
                    async with self._batch_lock:
                        if not batch.messages:
                            return
                        message = combined_message(batch.messages)
                        version = batch.version
                    if not self.store.is_channel_enabled(message.guild.id, message.channel.id):
                        await self.publish_translation(message, None)
                        return
                    mode = self.store.get_user_mode(message.guild.id, message.author.id)
                    result = await asyncio.to_thread(self.translate_content, message.content, mode, message.guild.id, message.channel.id)
                    async with self._batch_lock:
                        if not batch.messages:
                            return
                        # A source arriving during inference invalidates that partial result.
                        if version != batch.version:
                            batch.accepting = True
                            batch.started = asyncio.get_running_loop().time()
                            continue
                        await self.publish_translation(message, result, replace=batch.needs_move)
                        batch.needs_move = False
                    return
        except TranslationUnavailable:
            LOGGER.exception("Local translation unavailable for message %s; no fallback sent", message.id)
            async with self._batch_lock:
                if batch.messages:
                    await self.translation_failed(combined_message(batch.messages), batch.direction)
        except Exception:
            LOGGER.exception("Translation failed for message %s", message.id)
            async with self._batch_lock:
                if batch.messages:
                    await self.translation_failed(combined_message(batch.messages), batch.direction)
        finally:
            if batch.closed and self._batches.get(key) is batch:
                self._batches.pop(key, None)

    async def fetch_batch(self, channel, batch_id: int, *, separator: str | None = None):
        messages = [await channel.fetch_message(source_id) for source_id in self.store.get_batch_sources(batch_id)]
        return combined_message(messages, separator=separator)

    async def translation_failed(self, message: discord.Message, direction: tuple[str, str]) -> None:
        try:
            await self.publish_translation(
                message, TranslationResult(direction[0], direction[1],
                    TRANSLATION_ERRORS.get(direction[0], TRANSLATION_ERRORS['en'])),
                status="failed",
            )
            self.store.schedule_retry(message.id, message.guild.id, message.channel.id, time.time()+5)
        except Exception:
            LOGGER.exception("Could not replace translation placeholder for message %s", message.id)

    async def publish_translation(self, message: discord.Message, result, *, status: str = "complete", replace: bool = False) -> None:
        """Update existing chunks in place; only extra chunks need new posts."""
        links = self.store.get_source_links(message.id)
        chunks = split_for_discord(result.text) if result else []
        if not links and not chunks:
            return
        hook = await self.translation_webhook(message.channel)
        thread_args = {"thread": message.channel} if isinstance(message.channel, discord.Thread) else {}
        if any(link["webhook_id"] != hook.id for link in links):
            LOGGER.warning("Original webhook unavailable for message %s; cannot edit translation", message.id)
            return
        try:
            for index, chunk in enumerate(chunks):
                if index < len(links) and not replace:
                    posted = await hook.edit_message(
                        links[index]["translation_message_id"], content=chunk,
                        allowed_mentions=discord.AllowedMentions.none(), **thread_args,
                    )
                else:
                    posted = await hook.send(
                        chunk, username=message.author.display_name[:80],
                        avatar_url=str(message.author.display_avatar.url), wait=True,
                        allowed_mentions=discord.AllowedMentions.none(), **thread_args,
                    )
                self.store.link_translation(posted.id, message.id, message.channel.id,
                                            hook.id, result.source, result.target, status=status)
            if status in ('complete', 'pending'):
                self.store.cancel_retry(message.id)
            for link in (links if replace else links[len(chunks):]):
                try:
                    await hook.delete_message(link["translation_message_id"], **thread_args)
                except discord.NotFound:
                    pass
                self.store.unlink_translation(link["translation_message_id"])
        except discord.NotFound:
            self._webhooks.pop(getattr(hook, "channel_id", message.channel.id), None)
            LOGGER.warning("Webhook or translated message missing for original %s", message.id)

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        # Embed-only updates are common and do not require another translation.
        if payload.guild_id not in self.guild_ids or "content" not in payload.data:
            return
        channel = self.get_channel(payload.channel_id)
        if channel is None or not hasattr(channel, "fetch_message"):
            return
        if not self.store.is_channel_enabled(payload.guild_id, payload.channel_id):
            return
        try:
            live_batch = next((batch for batch in self._batches.values()
                               if any(m.id == payload.message_id for m in batch.messages)), None)
            edit_version = None
            if live_batch:
                edited = await channel.fetch_message(payload.message_id)
                async with self._batch_lock:
                    live_batch.messages = [edited if m.id == edited.id else m for m in live_batch.messages]
                    live_batch.version += 1
                    edit_version = live_batch.version
                    if live_batch.task and not live_batch.task.done():
                        live_batch.changed.set()
                        return
            async with self._translation_slots:
                # Check after acquiring the slot so edits during the initial generation
                # find its newly posted translation. Fetch latest text to avoid stale edits.
                links = self.store.get_source_links(payload.message_id)
                if not links:
                    return
                batch_id = links[0]["source_message_id"]
                source_ids = self.store.get_batch_sources(batch_id)
                message = await self.fetch_batch(channel, links[0]["source_message_id"])
                if message.author.bot or message.webhook_id:
                    return
                mode = self.store.get_user_mode(payload.guild_id, message.author.id)
                if mode == "off":
                    return
                result = await asyncio.to_thread(self.translate_content, message.content, mode, message.guild.id, message.channel.id)
                async with self._batch_lock:
                    if self.store.get_batch_sources(batch_id) != source_ids:
                        return
                    if live_batch and live_batch.version != edit_version:
                        return
                    await self.publish_translation(message, result)
        except (TranslationUnavailable, discord.HTTPException):
            LOGGER.exception("Could not update translation for edited message %s", payload.message_id)
            if 'message' in locals() and 'mode' in locals():
                direction = direction_for(message.content, mode, self.store.get_channel_languages(payload.guild_id, channel.id))
                if direction:
                    await self.translation_failed(message, direction)
        except Exception:
            LOGGER.exception("Edited translation failed for message %s", payload.message_id)

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        await self.clear_mirrored_reactions(payload)
        await self.delete_originals(payload.guild_id, payload.channel_id, {payload.message_id})

    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        for message_id in payload.message_ids:
            await self.clear_mirrored_reactions(SimpleNamespace(guild_id=payload.guild_id, message_id=message_id))
        await self.delete_originals(payload.guild_id, payload.channel_id, set(payload.message_ids))

    async def delete_originals(self, guild_id: int | None, channel_id: int, deleted_ids: set[int]) -> None:
        """Delete stale translations immediately, then rebuild surviving group text."""
        if guild_id not in self.guild_ids:
            return
        channel = self.get_channel(channel_id)
        if channel is None:
            return
        rebuild = []
        async with self._batch_lock:
            for post in self.store.temporary_posts(source_ids=deleted_ids):
                if post['guild_id'] == guild_id and post['channel_id'] == channel_id:
                    try:
                        await self.delete_temporary(post)
                    except discord.HTTPException:
                        LOGGER.warning('Could not delete requested translation for deleted original %s', post['message_id'])
            groups = {}
            for source_id in deleted_ids:
                links = self.store.get_source_links(source_id)
                if links and links[0]['channel_id'] == channel_id:
                    groups[links[0]['source_message_id']] = links
                elif not links:
                    batch_id = self.store.get_batch_id(source_id)
                    if batch_id is not None:
                        groups[batch_id] = []
            for batch_id, links in groups.items():
                try:
                    if links:
                        hook = await self.translation_webhook(channel)
                        if any(link['webhook_id'] != hook.id for link in links):
                            LOGGER.warning('Cannot delete translation: original webhook unavailable for %s', batch_id)
                            continue
                        thread_args = {'thread': channel} if isinstance(channel, discord.Thread) else {}
                        for link in links:
                            try:
                                await hook.delete_message(link['translation_message_id'], **thread_args)
                            except discord.NotFound:
                                pass
                            self.store.unlink_translation(link['translation_message_id'])
                    remaining = self.store.remove_batch_sources(batch_id, deleted_ids)
                    batches = list(self._batches.values()) + list(self._working_batches.values())
                    live = next((batch for batch in batches if any(m.id in deleted_ids for m in batch.messages)
                                 and batch.messages[0].channel.id == channel_id
                                 and any(m.id == batch_id for m in batch.messages)), None)
                    if live:
                        live.messages = [m for m in live.messages if m.id not in deleted_ids]
                        live.version += 1
                        live.changed.set()
                        live.needs_move = False
                        if not remaining:
                            live.closed = True
                            if live.expiry:
                                live.expiry.cancel()
                            for key, batch in list(self._batches.items()):
                                if batch is live:
                                    self._batches.pop(key, None)
                    if remaining and links and self.store.is_channel_enabled(guild_id, channel_id):
                        message = combined_message(live.messages) if live else await self.fetch_batch(channel, remaining[0])
                        direction = (links[0]['source_language'], links[0]['target_language'])
                        await self.publish_translation(message, TranslationResult(*direction, TRANSLATING.get(direction[1], 'Translating…')), status='pending')
                        if not live or not live.task or live.task.done():
                            rebuild.append((remaining[0], remaining))
                except discord.HTTPException:
                    LOGGER.exception('Could not remove translation for deleted original %s', batch_id)
        for batch_id, source_ids in rebuild:
            try:
                async with self._translation_slots:
                    message = await self.fetch_batch(channel, batch_id)
                    mode = self.store.get_user_mode(guild_id, message.author.id)
                    result = await asyncio.to_thread(self.translate_content, message.content, mode, message.guild.id, message.channel.id)
                    async with self._batch_lock:
                        if self.store.get_batch_sources(batch_id) == source_ids:
                            await self.publish_translation(message, result)
            except (TranslationUnavailable, discord.HTTPException):
                LOGGER.exception('Could not rebuild translation after deleting from group %s', batch_id)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """A 🚩 on an automatic reply stores that pair for later improvement."""
        if str(payload.emoji) == CORRECTED_REACTION:
            return  # A status indicator, not a regenerate command or mirror.
        if str(payload.emoji) in FLAG_LANGUAGES:
            await self.request_flag_translation(payload, FLAG_LANGUAGES[str(payload.emoji)])
            return
        if str(payload.emoji) != '🚩':
            await self.sync_reaction(payload, added=True)
            return
        if (
            str(payload.emoji) != "🚩"
            or payload.guild_id not in self.guild_ids
            or (self.user is not None and payload.user_id == self.user.id)
        ):
            return
        channel = self.get_channel(payload.channel_id)
        if channel is None or not hasattr(channel, "fetch_message"):
            return
        try:
            translation_message = await channel.fetch_message(payload.message_id)
            link = self.store.get_translation_link(payload.message_id)
            if link is None and not translation_message.author.bot and not translation_message.webhook_id:
                original = translation_message
                links = self.store.get_source_links(original.id)
                if not links:
                    if not self.store.is_channel_enabled(payload.guild_id, payload.channel_id):
                        return
                    mode = self.store.get_user_mode(payload.guild_id, original.author.id)
                    direction = direction_for(original.content, mode,
                        self.store.get_channel_languages(payload.guild_id, payload.channel_id))
                    if direction is None:
                        return
                    async with self._batch_lock:
                        links = self.store.get_source_links(original.id)
                        if not links:
                            await self.publish_translation(original, TranslationResult(*direction,
                                TRANSLATING.get(direction[1], 'Translating…')), status='pending')
                            links = self.store.get_source_links(original.id)
                            self.store.schedule_retry(original.id, payload.guild_id, payload.channel_id, time.time())
                if not links:
                    return
                translation_message = await channel.fetch_message(links[0]['translation_message_id'])
                link = self.store.get_translation_link(translation_message.id)
            if link is not None:
                if link["status"] not in ('complete', 'failed', 'pending'):
                    return
                if link["channel_id"] != payload.channel_id or link["webhook_id"] != translation_message.webhook_id:
                    return
                source_message = await self.fetch_batch(channel, link["source_message_id"], separator="\n")
                source_language = link["source_language"]
                target_language = link["target_language"]
                translated_text = translation_message.content
            elif source_ids := self.store.temporary_sources(payload.message_id):
                temporary = next((post for post in self.store.temporary_posts() if post['message_id'] == payload.message_id), None)
                if not temporary or temporary['webhook_id'] != translation_message.webhook_id:
                    return
                originals = [await channel.fetch_message(source_id) for source_id in source_ids]
                source_message = combined_message(originals, separator='\n')
                source_language, target_language = temporary['source_language'], temporary['target_language']
                translated_text = translation_message.content.partition('\n')[2]
            elif self.user and translation_message.author.id == self.user.id and translation_message.reference:
                # Existing flagged replies remain reportable after the switch.
                if translation_message.reference.message_id is None:
                    return
                source_message = await channel.fetch_message(translation_message.reference.message_id)
                if translation_message.content.startswith(f"{FLAG['ru']} "):
                    source_language, target_language = "en", "ru"
                elif translation_message.content.startswith(f"{FLAG['en']} "):
                    source_language, target_language = "ru", "en"
                else:
                    return
                translated_text = translation_message.content.removeprefix(f"{FLAG[target_language]} ")
            else:
                return

            created = self.store.add_feedback(
                guild_id=payload.guild_id,
                channel_id=payload.channel_id,
                source_message_id=source_message.id,
                translation_message_id=translation_message.id,
                source_author_id=source_message.author.id,
                reporter_id=payload.user_id,
                source_language=source_language,
                target_language=target_language,
                source_text=source_message.content,
                translated_text=translated_text,
                corrected_text=None,
                flag_message_id=payload.message_id,
                note=('Automatic translation failed; this report contains the displayed error, not model output.'
                      if link is not None and link['status'] == 'failed' else
                      'Translation pending or missing when flagged.' if link is not None and link['status'] == 'pending' else None),
            )
            if created:
                LOGGER.info(
                    "Stored translation feedback for message %s from user %s",
                    translation_message.id,
                    payload.user_id,
                )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not capture flagged translation %s", payload.message_id)

    async def request_flag_translation(self, payload, target: str) -> None:
        if payload.guild_id not in self.guild_ids or (self.user and payload.user_id == self.user.id):
            return
        channel = self.get_channel(payload.channel_id)
        if channel is None or not hasattr(channel, 'fetch_message'):
            return
        if getattr(payload, 'member', None) and payload.member.bot:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
            link = self.store.get_translation_link(message.id)
            source_ids = self.store.temporary_sources(message.id)
            if link:
                source_ids = self.store.get_batch_sources(link['source_message_id'])
            elif not source_ids:
                if message.author.bot or message.webhook_id:
                    return
                source_ids = [message.id]
            originals = [await channel.fetch_message(source_id) for source_id in source_ids]
            source = combined_message(originals)
            key = (payload.guild_id, payload.channel_id, tuple(source_ids), target)
            task = self._flag_requests.get(key)
            if task is None:
                task = asyncio.create_task(self.deliver_flag_translation(source, source_ids, payload.user_id, target))
                self._flag_requests[key] = task
                def done(completed):
                    if self._flag_requests.get(key) is completed:
                        self._flag_requests.pop(key, None)
                task.add_done_callback(done)
            delivered = await asyncio.shield(task)
            if delivered:
                try:
                    await message.remove_reaction(payload.emoji, discord.Object(id=payload.user_id))
                except discord.Forbidden:
                    LOGGER.warning('Translation delivered, but removing country flags requires Manage Messages in channel %s', channel.id)
        except (discord.HTTPException, TranslationUnavailable):
            LOGGER.exception('Could not deliver requested %s translation for message %s', target, payload.message_id)

    async def deliver_flag_translation(self, source, source_ids: list[int], requester_id: int, target: str) -> bool:
        from .translation import detect_multilingual
        language = await asyncio.to_thread(detect_multilingual, source.content, tuple(LANGUAGES))
        if language is None:
            return False
        hook = await self.translation_webhook(source.channel)
        thread_args = {'thread': source.channel} if isinstance(source.channel, discord.Thread) else {}
        posts = []
        pending_until = int(time.time() + self.settings.ollama_timeout_seconds + 600)
        heading = f'**{LANGUAGES[target]}** · requested by <@{requester_id}> · deletes <t:{pending_until}:R>'
        try:
            posted = await hook.send(heading + '\n' + TRANSLATING.get(target, 'Translating…'),
                username=source.author.display_name[:80], avatar_url=str(source.author.display_avatar.url),
                wait=True, allowed_mentions=discord.AllowedMentions.none(), **thread_args)
            posts.append(posted.id)
            self.store.add_temporary(posted.id, source.guild.id, source.channel.id, hook.id,
                                     language, target, pending_until, source_ids)
            result = await self.translate_on_demand(source.content, target, language)
            if result is None:
                if language != target:
                    raise TranslationUnavailable('The model did not produce a translation in the requested language.')
                result = TranslationResult(language, target, source.content)
            async with self._batch_lock:
                # Serialize all chunks with original deletion and expiry, so a
                # deleted group cannot grow new chunks halfway through delivery.
                current = [await source.channel.fetch_message(source_id) for source_id in source_ids]
                if combined_message(current).content != source.content:
                    raise TranslationUnavailable('Original changed during the requested translation.')
                if not self.store.temporary_sources(posted.id):
                    return False
                expires_at = int(time.time() + reading_seconds(result.text))
                heading = f'**{LANGUAGES[target]}** · requested by <@{requester_id}> · deletes <t:{expires_at}:R>'
                for index, chunk in enumerate(split_for_discord(result.text, limit=1700)):
                    content = heading + '\n' + chunk
                    if index == 0:
                        await hook.edit_message(posted.id, content=content, allowed_mentions=discord.AllowedMentions.none(), **thread_args)
                        message_id = posted.id
                    else:
                        extra = await hook.send(content, username=source.author.display_name[:80],
                            avatar_url=str(source.author.display_avatar.url), wait=True,
                            allowed_mentions=discord.AllowedMentions.none(), **thread_args)
                        posts.append(extra.id)
                        message_id = extra.id
                    self.store.add_temporary(message_id, source.guild.id, source.channel.id, hook.id,
                                             language, target, expires_at, source_ids)
            return True
        except (discord.HTTPException, TranslationUnavailable):
            for message_id in posts:
                post = next((row for row in self.store.temporary_posts() if row['message_id'] == message_id), None)
                if post:
                    try:
                        await self.delete_temporary(post)
                    except discord.HTTPException:
                        pass
            raise

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if str(payload.emoji) == CORRECTED_REACTION:
            return
        if str(payload.emoji) != '🚩' and str(payload.emoji) not in FLAG_LANGUAGES:
            await self.sync_reaction(payload, added=False)
            return
        if str(payload.emoji) != "🚩" or payload.guild_id not in self.guild_ids:
            return
        if self.store.remove_feedback(payload.message_id, payload.user_id):
            LOGGER.info(
                "Removed translation feedback for message %s from user %s",
                payload.message_id,
                payload.user_id,
            )

    async def sync_reaction(self, payload, *, added):
        if (payload.guild_id not in self.guild_ids or not self.user
                or payload.user_id == self.user.id or getattr(getattr(payload, 'member', None), 'bot', False)):
            return
        channel = self.get_channel(payload.channel_id)
        if not channel or not hasattr(channel, 'fetch_message'):
            return
        async with self._reaction_lock:
            try:
                if added:
                    link = self.store.get_translation_link(payload.message_id)
                    temporary = next((r for r in self.store.temporary_posts() if r['message_id'] == payload.message_id), None)
                    if not link and not temporary:
                        return
                    translated = await channel.fetch_message(payload.message_id)
                    row = link or temporary
                    if row['channel_id'] != channel.id or translated.webhook_id != row['webhook_id']:
                        return
                    source_id = link['source_message_id'] if link else self.store.temporary_sources(payload.message_id)[0]
                    original = await channel.fetch_message(source_id)
                    await original.add_reaction(payload.emoji)
                    self.store.mirror_reaction(payload.message_id, source_id, channel.id, str(payload.emoji), payload.user_id)
                else:
                    removed = self.store.unmirror_reaction(payload.message_id, str(payload.emoji), payload.user_id)
                    for row in removed:
                        original = await channel.fetch_message(row['source_id'])
                        await original.remove_reaction(payload.emoji, self.user)
            except discord.HTTPException:
                LOGGER.warning('Could not sync reaction for translation %s; check Add Reactions and channel access', payload.message_id)

    async def on_raw_reaction_clear(self, payload):
        await self.clear_mirrored_reactions(payload)

    async def on_raw_reaction_clear_emoji(self, payload):
        await self.clear_mirrored_reactions(payload, str(payload.emoji))

    async def clear_mirrored_reactions(self, payload, emoji=None):
        if payload.guild_id not in self.guild_ids or not self.user:
            return
        async with self._reaction_lock:
            for row in self.store.unmirror_reaction(payload.message_id, emoji):
                try:
                    channel = self.get_channel(row['channel_id'])
                    if channel:
                        original = await channel.fetch_message(row['source_id'])
                        parsed = discord.PartialEmoji.from_str(row['emoji'])
                        await original.remove_reaction(parsed, self.user)
                except discord.HTTPException:
                    LOGGER.warning('Could not remove mirrored reaction from original %s', row['source_id'])


def create_bot(settings: Settings) -> TranslatorBot:
    bot = TranslatorBot(settings)

    async def allowed_server(interaction: discord.Interaction) -> bool:
        if interaction.guild_id in bot.guild_ids:
            return True
        await interaction.response.send_message("This bot is not enabled for this server.", ephemeral=True)
        return False

    bot.tree.interaction_check = allowed_server

    @bot.tree.command(name="translate-help", description="How to use translations, language modes, and bad-translation flags")
    async def translate_help(interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="English ↔ Russian translation · Помощь",
            description=(
                "Write normally: English becomes Russian, Russian becomes English. "
                "Your original stays; a translation follows under your name and avatar, without flags or pings. "
                "Editing your original updates its translation in place.\n"
                "Failed translations retry automatically, up to three additional attempts.\n"
                "Deleting your original removes its translation; grouped translations keep only surviving messages.\n"
                "Consecutive messages within the burst window share one translation, moved below the latest originals.\n"
                "Пишите как обычно: английский переводится на русский, русский — на английский. "
                "Оригинал сохраняется, перевод появляется с вашим именем и аватаром без уведомления."
            ),
            color=0x5865F2,
        )
        embed.add_field(
            name="Your settings · Ваши настройки",
            value=(
                "`/translate-mode` — choose **Auto**, **English → Russian**, **Russian → English**, or **Off**. "
                "Applies to your messages throughout this server and survives restarts.\n"
                "Выберите автоматический режим, направление перевода или отключение для своих сообщений.\n"
                "`/translate-status` — check your mode and whether this channel is enabled."
            ),
            inline=False,
        )
        embed.add_field(
            name="Private translation · Личный перевод",
            value="`/translate-now text:… target:…` — choose an output language; only you see the result.",
            inline=False,
        )
        embed.add_field(
            name="Report a mistake · Сообщить об ошибке",
            value=(
                "React with 🚩 on the translation or its original (including stuck/missing translations). "
                "In enabled channels an original with no translation is queued for recovery. The original and translation are saved locally "
                "for later review and improvement. Remove your reaction to withdraw the report.\n"
                "Поставьте 🚩 на переводе или оригинале, в том числе если перевод завис или отсутствует. Удалите реакцию, чтобы отменить сообщение об ошибке. "
                "Reports do not automatically retrain the model."
                " 🔄 means this translation was corrected after review (not a regenerate button). "
                "The bot adds it after verifying the fix, then removes 🚩 when permissions allow; review records are retained."
            ),
            inline=False,
        )
        embed.add_field(
            name="Country flags · Перевод по флагу",
            value=(
                "React on an original or translated message with 🇬🇧/🇺🇸 English, 🇷🇺 Russian, 🇷🇴 Romanian, "
                "🇫🇷 French, 🇩🇪 German, 🇪🇸 Spanish, or another supported country flag. "
                "England, Scotland and Wales also select English. Multilingual countries use the defaults below. "
                "Unlisted flags are unsupported, not an English fallback. "
                "The requested translation is PUBLIC in the same channel, then automatically deleted. "
                "Reading time: 100 words/minute + 30 seconds, minimum 90 seconds, starting when ready. "
                "Your flag is removed after delivery (requires the bot's Manage Messages permission)."
            ),
            inline=False,
        )
        embed.add_field(
            name="Supported flag defaults",
            value='\n'.join(
                f"{name}: " + ' '.join(
                    flag for flag, target in FLAG_LANGUAGES.items()
                    if target == code and flag not in SUBDIVISION_FLAGS.values()
                ) for code, name in LANGUAGES.items()
            ) + '\nEngland / Scotland / Wales: English. Chinese flags: Simplified Chinese.',
            inline=False,
        )
        embed.add_field(
            name="Emoji reactions",
            value="Ordinary reactions on a translation also appear on its original as one bot reaction. "
                  "Multiple people share that mirror; removing the last reaction clears it. "
                  "Country flags and 🚩 keep their translation-request/report functions.",
            inline=False,
        )
        embed.add_field(
            name="Server admins · Администраторам",
            value=(
                "`/translate-channel enabled:True/False` — enable or disable this channel.\n"
                "`/translate-feedback-count` — count reports awaiting review.\n"
                "`/translate-languages languages:en,ru,ro` — set this channel's automatic language list.\n"
                "Add `global_default:True` to change the server default instead. These admin commands require **Manage Server**. "
                "Defaults are English/Russian; changing languages does not enable a disabled channel."
            ),
            inline=False,
        )
        embed.set_footer(text="Translations run on the host machine. Normal conversations are not stored; flagged examples are.")
        help_text = f"**{embed.title}**\n{embed.description}\n\n" + "\n\n".join(
            f"**{field.name}**\n{field.value}" for field in embed.fields
        )
        pages = split_for_discord(help_text)
        await interaction.response.send_message(pages[0], ephemeral=True)
        for page in pages[1:]:
            await interaction.followup.send(page, ephemeral=True)

    @bot.tree.command(name="translate-mode", description="Choose how your messages are translated")
    @app_commands.describe(mode="Your automatic translation preference")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Auto-detect English/Russian", value="auto"),
            app_commands.Choice(name="Always English to Russian", value="en_to_ru"),
            app_commands.Choice(name="Always Russian to English", value="ru_to_en"),
            app_commands.Choice(name="Off for my messages", value="off"),
        ]
    )
    async def translate_mode(
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
    ) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside the server.", ephemeral=True)
            return
        bot.store.set_user_mode(interaction.guild_id, interaction.user.id, mode.value)
        await interaction.response.send_message(
            f"Translation preference: **{MODE_LABELS[mode.value]}**.",
            ephemeral=True,
        )

    @bot.tree.command(name="translate-status", description="Show your translation preference here")
    async def translate_status(interaction: discord.Interaction) -> None:
        if not interaction.guild_id or not interaction.channel_id:
            await interaction.response.send_message("Use this command inside the server.", ephemeral=True)
            return
        mode = bot.store.get_user_mode(interaction.guild_id, interaction.user.id)
        channel_enabled = bot.store.is_channel_enabled(interaction.guild_id, interaction.channel_id)
        await interaction.response.send_message(
            f"Your mode: **{MODE_LABELS[mode]}**\n"
            f"This channel: **{'enabled' if channel_enabled else 'disabled'}**\n"
            f"Channel languages: **{', '.join(LANGUAGES[code] for code in bot.store.get_channel_languages(interaction.guild_id, interaction.channel_id))}**",
            ephemeral=True,
        )

    @bot.tree.command(name="translate-now", description="Privately translate text without posting it")
    @app_commands.describe(text="Text to translate", target="Output language")
    @app_commands.choices(
        target=[app_commands.Choice(name=name, value=code) for code, name in LANGUAGES.items()]
    )
    async def translate_now(
        interaction: discord.Interaction,
        text: str,
        target: app_commands.Choice[str],
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await bot.translate_on_demand(text, target.value)
            output = result.text if result else "No translation needed (already in that language, or no translatable text)."
        except Exception:
            LOGGER.exception("Manual translation failed")
            output = "Translation failed. Check the bot log and installed models."
        await interaction.followup.send(output[:1_990], ephemeral=True)

    @bot.tree.command(name="translate-channel", description="Enable or disable automatic translation here")
    @app_commands.describe(enabled="Whether messages in this channel are translated")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def translate_channel(interaction: discord.Interaction, enabled: bool) -> None:
        if not interaction.guild_id or not interaction.channel_id:
            await interaction.response.send_message("Use this command inside a server channel.", ephemeral=True)
            return
        bot.store.set_channel_enabled(interaction.guild_id, interaction.channel_id, enabled)
        await interaction.response.send_message(
            f"Automatic translation is now **{'enabled' if enabled else 'disabled'}** here.",
            ephemeral=True,
        )

    @bot.tree.command(name='translate-languages', description='Set the automatic language list for this channel or server')
    @app_commands.describe(languages='Two or more comma-separated language codes, e.g. en,ru,ro',
                           global_default='Change the server default instead of this channel')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def translate_languages(interaction: discord.Interaction, languages: str, global_default: bool = False) -> None:
        if not interaction.guild_id or not interaction.channel_id:
            await interaction.response.send_message('Use this command in a server channel.', ephemeral=True)
            return
        try:
            codes = parse_languages(languages)
        except ValueError as exc:
            await interaction.response.send_message(f'{exc}\nSupported: ' + ', '.join(LANGUAGES), ephemeral=True)
            return
        bot.store.set_languages(interaction.guild_id, codes, None if global_default else interaction.channel_id)
        await interaction.response.send_message(
            f"{'Server default' if global_default else 'Channel'} languages: **{', '.join(LANGUAGES[code] for code in codes)}**. "
            'Auto mode translates into the other listed languages. Explicit user directions still override the list. '
            'Channel enabled/disabled settings are unchanged.', ephemeral=True,
        )

    @translate_channel.error
    @translate_languages.error
    async def translate_channel_error(
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the Manage Server permission to change channel translation.",
                ephemeral=True,
            )
            return
        LOGGER.error("Slash command error: %s", error)

    @bot.tree.command(name="translate-feedback-count", description="Count translations awaiting review")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def translate_feedback_count(interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside the server.", ephemeral=True)
            return
        count = bot.store.open_feedback_count(interaction.guild_id)
        await interaction.response.send_message(
            f"**{count}** flagged translation{'s' if count != 1 else ''} awaiting review.",
            ephemeral=True,
        )

    return bot
