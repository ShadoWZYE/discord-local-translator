# Local English ↔ Russian Discord Translator

A self-hosted Discord bot with a Windows setup workflow. It detects
English/Russian messages, translates them with a local TranslateGemma 12B model, and posts a
compact webhook message with the sender's name and avatar, without a reply preview,
language flag, or ping. The original message remains intact. Discord still displays its APP badge.
The webhook first posts **Перевожу…** for Russian output or **Translating…** for
English output, then edits that same message into the result. Failures replace the placeholder with a short
retry instruction in the ORIGINAL message's language (the translating placeholder
uses the target language). Pending placeholders and failure notices can be flagged;
reports distinguish unfinished/failed posts from translated output.
Consecutive messages from one person in one channel stay in a rolling burst until
15 seconds of inactivity (`BURST_WINDOW_SECONDS` in `.env`). A burst can extend
even after its translation is complete: the bot posts a new localized placeholder
at the end, removes its older translation, and updates the new post with the full
accumulated translation. A replacement is posted successfully before old webhook
messages are removed. Fast arrivals are collected after a 1.5-second pause, with
up to 5 seconds of batching before inference starts. If another source arrives
during inference, stale partial output is discarded and the full group is retried.
Another person's message or a change in language direction starts a separate
group. Groups are capped at 1,900 source characters; long output still
uses Discord-sized chunks. Edits and 🚩 reports cover the whole group, including
after a restart. Original messages are kept individually; this reduces translation
spam, not the sender's own posts. Reposting removes reactions on the old webhook
message; previously logged feedback is retained. A bot restart ends any live burst
window, but existing groups can still be edited and reported. Interrupted pending
placeholders are recovered in place after startup. Messages arriving while the
Gateway is disconnected are not history-wide backfilled: 🚩 on their original
queues a missing translation in an enabled channel. Pending placeholders and
originals are reportable; flags are tracked at their actual message locations.
Unfinished message fragments are joined with spaces so a sentence split across
posts is interpreted as one sentence. Completed messages (sentence punctuation
or a closing-parenthesis smiley) retain a newline between posts. All are translated
together with full context. Newlines actually typed inside
a message are preserved, including lists and paragraphs. Flagged feedback retains
the original per-message boundaries. Run `scripts/check_context.py` for live local
model checks of split sentences, slang, negation, and multiline formatting.
Editing an original updates its existing webhook translation in place, including
after a bot restart. This applies to messages translated with the webhook format.
Long translations may need extra chunks at the end of chat; shorter edits remove
surplus translation chunks. Discord does not support inserting or backdating messages
in history. Edits while the bot is offline are not replayed automatically.
Deleting an original removes all of its translation chunks, including pending
placeholders. If it belongs to a grouped burst, the old translation is removed
and rebuilt from only the surviving originals. Bulk deletions are supported, and
in-progress inference cannot restore deleted text. Stored mappings survive bot
restarts; deletion events missed while the bot is offline are not reconciled automatically.

## Discord setup (one-time)

1. Open the [Discord Developer Portal](https://discord.com/developers/applications),
   create an application, and add a bot.
2. On **Bot**, enable **Message Content Intent** and copy/reset the bot token.
3. On **OAuth2 → URL Generator**, select scopes `bot` and `applications.commands`.
4. Select bot permissions: **View Channels**, **Send Messages**, **Read Message
   History**, **Send Messages in Threads**, **Add Reactions**, and **Manage Webhooks**. Open the generated URL and add
   the bot to your server.
5. Install Python 3.13, then run `./setup.ps1`. Open `.env`, set `DISCORD_TOKEN`,
   and set `DISCORD_GUILD_ID` to your server ID.
6. Run `./start.ps1`.
7. Once confirmed working, run `./register-startup.ps1` to launch the bot at
   Windows sign-in. `./unregister-startup.ps1` removes that task.

Administrator is not needed. **Manage Messages** is needed to remove someone
else's country-flag/report reaction after delivery or review. Manage Webhooks lets the bot
create one translation webhook per channel and post with the sender's name/avatar.

## Commands

- `/translate-help`: private English/Russian guide to all commands and 🚩 reporting.
- `/translate-mode`: auto-detect (default), force a direction, or opt out.
- `/translate-status`: show your mode and whether this channel is active.
- `/translate-now`: translate text privately, visible only to you.
- `/translate-channel`: admins can enable/disable automatic translation here.
- `/translate-languages languages:en,ru,ro`: set this channel's automatic language
  list (two or more). Add `global_default:True` to set the server default instead.
- `/translate-feedback-count`: admins can see how many examples need review.

To report a bad translation, react to the translated webhook message with 🚩. The original and
translation are immediately stored locally, once per reporter, with no form or
extra channel message. Only explicitly flagged examples are stored; ordinary
conversation text is not logged. Message IDs and language directions are stored
to link webhook translations back to their originals across restarts. Feedback is kept in the `translation_feedback`
SQLite table for glossary improvements or a later fine-tuning dataset. Removing
your 🚩 reaction removes your corresponding local feedback record.
If the model omits or damages protected mentions, URLs, or code, the translator
retries once using the full source with literal syntax. The retry must preserve
every protected span exactly, in order, without omissions or duplicates; unsafe
output is still rejected. Successful first attempts do not incur a retry.

Flagged slang regressions inform source-matched phrase guidance in both directions:
preserve profanity without inventing it, narrow victories, sleep/waking details,
sexual jokes, slurs without changing their meaning, and prison/toilet idioms.
The bot no longer assumes that mentioning a bot makes the whole message a software
discussion. Only hints matching the actual source are included. This is prompt/
glossary guidance, not training new model weights or a guarantee of perfect slang.
There is no profanity checker, offensive-word gate, or required swear vocabulary.
The bot does not judge whether a speaker is rude, nor reject/retry translations
based on profanity, slur choices, or perceived tone. Users can flag inaccuracies.
Only protected-syntax and line-layout checks can trigger a full-context retry;
they share a maximum of two model calls total. Successful first attempts remain
one call. Language, nonempty-output and truncation checks remain. Reports remain available
for further review, and replaying them does not alter their stored originals.
Literal `~` and `~~` delimiters are protected and must remain in order; a tilde
is never a paragraph separator. The translated message must retain the source's
newline count. Single inline tildes use schema-constrained JSON translation fields
in one full-context inference. The bot restores exact separator spacing and
position itself, rather than trusting model punctuation or newline counts.
Messages with internal blank lines use the same structured path; blank fields
are fixed by the schema, including whitespace-only lines. Translated fields
cannot introduce newline characters. Fields cannot disappear, become empty, or move protected
tokens to another field. `scripts/check_layout.py` runs live local regressions.
Strikethrough delimiters retain protected-syntax validation.
Line-ending tildes are reattached to their original lines outside inference,
while the full text is translated together. Roleplay `*`/`**` delimiters are
preserved, including the boundary between italic narration and spoken words.
Unnecessary model-generated apostrophe escapes are removed outside protected code.
Russian line-ending smileys become emoji: `)` → 🙂, `))` → 😄, and `)))` or
more → 😆. Balanced parentheses and protected code/URLs are not converted.
Russian laughter-only text such as `Ахахаха` is rendered phonetically as
`Ahahaha` for English without model inference, preserving casing and punctuation.
Sighs such as `Ах` and sentences containing other words still use the model.
After a flagged translation is corrected and verified in place, the bot adds
🔄 before removing 🚩. This is a visible repair indicator, not a regenerate
button, and is not mirrored onto the original. Permission failures are retried;
review records remain saved. The marker is only added if the post still matches
the verified correction.

Ordinary emoji reactions on this bot's tracked automatic/temporary webhook posts
mirror to the original as one **bot-owned** reaction, not as the reacting user.
For a grouped translation, the anchor is the first surviving original. Multiple
reactors/chunks share that reaction; it is removed when the last tracked reactor
withdraws it. Membership persists across restarts. Reaction clearing and webhook
deletion clear the mirrored contribution. Country flags and 🚩 remain commands
on translation posts rather than mirrored reactions. Add Reactions and channel
access are needed; the bot cannot impersonate a user's reaction or count.

Automatic translation failures receive up to three additional retries with
5/15/45-second backoff, editing the existing failure post rather than duplicating
it. Retry IDs/attempts persist across restarts. Deleted/changed originals, disabled
channels, changed preferences, and completed translations prevent stale retries.
Country-flag and private command requests also retry local-model failures up to
three times, releasing the inference slot while waiting. No profanity checks
have been reintroduced. Failures that still cannot be resolved remain visible.

`scripts/correct_feedback.py stage IDS` creates candidates for explicit reports;
inspect them before `apply IDS` updates the existing webhook posts. Source text
is rechecked before application, then the published correction is verified.
Only applied, corrected reports have their 🚩 removed automatically. Removing
others' report reactions requires Manage Messages; cleanup retries every five
minutes if unavailable. Reviewed/corrected records remain stored. A later new
flag on the same post reopens a report and archives the previous reviewed record.
Short English pronoun/auxiliary clauses receive a narrow detection override when
English is an allowed source, avoiding Spanish/Portuguese misclassification of
phrases such as "I do so already." Mild vulgar adjectives such as "shitty" can
use contextual equivalents such as "хреновый" without an erroneous rejection.

Country flags request a temporary **PUBLIC** translation in the same channel;
they do not create a private response. Examples: 🇬🇧/🇺🇸 English, 🇷🇺 Russian,
🇷🇴 Romanian, 🇫🇷 French, 🇩🇪 German, 🇪🇸 Spanish, 🇮🇹 Italian, 🇵🇹/🇧🇷 Portuguese,
🇳🇱 Dutch, 🇵🇱 Polish, 🇺🇦 Ukrainian, 🇹🇷 Turkish, 🇨🇿 Czech, 🇬🇷 Greek, 🇸🇪 Swedish,
🇫🇮 Finnish, 🇭🇺 Hungarian, 🇧🇬 Bulgarian, 🇸🇦 Arabic, 🇮🇱 Hebrew, 🇯🇵 Japanese,
🇰🇷 Korean, 🇨🇳 Chinese (Simplified). React on the original or on a translation;
requests on translated groups use their full original text. A localized placeholder
appears immediately. The completed request displays a deletion time and uses
100 words/minute plus 30 seconds, with a minimum of 90 seconds. CJK text receives
a character-based reading allowance. The clock starts after translation, not when
queued. All chunks share the full translation's reading time. Expiry IDs/timestamps
persist across restarts (cleanup checks every five seconds); if the bot is offline
or lacks permissions, deletion waits until it can act. Deleting any original in a
temporary request removes that request too. Simultaneous requests for the same
original and target share one generation. The requester's flag is removed only
after successful delivery, provided the bot has Manage Messages. 🚩 still reports
bad translations, including temporary ones; stored reports survive timed cleanup.

There are 186 flag shortcuts across the existing 23 supported languages. The full
live list is in `/translate-help`; the explicit territory-code mapping is in
`translator_bot/languages.py`. England (`:england:`), Scotland (`:scotland:`),
and Wales (`:wales:`) use their complete Unicode subdivision sequences and select
English, independently of the UK (`:flag_gb:` / `:uk:`) flag. A plain black flag,
pirate flag, custom server emoji, EU/UN flag, or an unlisted country's flag does
not request a translation. Discord's built-in emoji aliases resolve to the same
Unicode emoji; this is not a match on a custom emoji's name.

Country flags are shortcuts, not language detection. Multilingual countries use
explicit defaults: Canada and Singapore → English; Belgium → Dutch; Switzerland
→ German; Luxembourg → French; Kazakhstan → Russian; Moldova → Romanian.
Hong Kong, Macau and Taiwan select the currently supported Simplified Chinese
target, not a separate Traditional Chinese target. Scotland/Wales select English,
not Scottish Gaelic/Welsh. Other territory defaults use Unicode CLDR release 47's
most-spoken national official/de-facto official language (or most-spoken language
where there is no such designation). Only supported targets are enabled; e.g.
India's Hindi default is not currently supported and does not fall back to English.
Use `/translate-now` to choose a different supported language explicitly.
Source: https://github.com/unicode-org/cldr/blob/release-47/common/supplemental/supplementalData.xml

Allowed servers default to enabled unless listed in `DISCORD_DEFAULT_OFF_GUILD_IDS`;
listed servers default to disabled, including newly created channels.
An admin can run `/translate-channel enabled:True` in each channel to opt in.
`/translate-now` remains available for private on-demand translation.
Only configured server IDs are serviced, even if the bot is installed elsewhere.
Settings persist independently per server in local SQLite. Allowed server IDs
and default-off server IDs are configured in `.env`.
New servers' global language defaults are English/Russian.
Language lists do not change whether a channel is enabled.
Auto mode translates into the other listed languages, grouping multiple target
languages in one post. Explicit English→Russian/Russian→English user modes override
the channel list; Off stays off. Each extra target needs another model generation.
Language detection is local; very short or mixed-language texts can be ambiguous.
The additional language choices are not all quality-certified: English/Russian,
Romanian, and French have live smoke checks, but native-speaker review is still
needed for nuanced translations in newly enabled languages.
Attachment-only, sticker-only, emoji-only, numeric/punctuation-only, URL-only,
mention-only (including `@everyone`/`@here`), and code-only messages are skipped,
including in forced language modes.
Captions containing English or Russian text are still translated.
Mentions, custom emoji, URLs, and inline/fenced code are preserved rather than translated.
The model is kept resident on the RX 6750 XT for responsive replies. Edit
`glossary.json` to add project names, technical terminology, or chat idioms.
Translation guidance preserves profanity, sarcasm, humour, and affectionate tone;
it does not intentionally censor swear words. Relevant messages receive compact
phrase guidance to help avoid literal idiom translations and misplaced insults. These are prompt
and glossary improvements, not model fine-tuning.

Replay open reports locally without changing them:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\review_feedback.py
```

The replay prints private reported message pairs in your terminal. There is no
translation-result cache; clearing reviewed reports removes only their SQLite
feedback rows, not server preferences, message links, or downloaded model files.

## Local operation

The internet is needed during initial package/model installation and for the
ongoing Discord connection. Message text is processed by Ollama on `127.0.0.1`
and is not sent to a translation service. If the quality model is unavailable,
the bot fails closed instead of silently using a weaker or remote translator.

Run tests with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
