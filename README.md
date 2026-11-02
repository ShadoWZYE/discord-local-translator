# Local Multilingual Discord Translator

A self-hosted Discord bot with a Windows setup workflow. It detects
messages in configured languages, translates them with a local TranslateGemma 12B model, and posts a
compact webhook message with the sender's name and avatar, without a reply preview,
language flag, or ping. The original message remains intact. Discord still displays its APP badge.
The webhook first posts **Перевожу…** for Russian output or **Translating…** for
English output, then edits that same message into the result. Failures replace the placeholder with a short
retry instruction in the ORIGINAL message's language (the translating placeholder
uses the target language). Pending placeholders and failure notices can be flagged;
reports distinguish unfinished/failed posts from translated output.
Failed automatic translations add a bot-owned 🚩 and a persistent review record.
Successful recovery resolves only that automatic record and removes only the
bot's flag; user reports remain for review. Missing reaction permissions do not
discard the report or stop retries.

## Language support

English and Russian are the default pair, not the full scope. The language registry currently offers **23 selectable languages**: English (en), Russian (ru), Romanian (ro), French (fr), German (de), Spanish (es), Italian (it), Portuguese (pt), Dutch (nl), Polish (pl), Ukrainian (uk), Turkish (tr), Czech (cs), Greek (el), Swedish (sv), Finnish (fi), Hungarian (hu), Bulgarian (bg), Arabic (ar), Hebrew (he), Japanese (ja), Korean (ko), and Chinese Simplified (zh).

Use `/translate-languages languages:en,ru,ro` to configure a channel's automatic language set. Country-flag reactions request translations into supported target languages; flags are shortcuts, not language detection. Language choices are defined in `translator_bot/languages.py`. Selectable support does not mean every language pair has equal quality validation; the pair-specific English/Russian safeguards documented below remain relevant.

## Message handling

Carl-bot numbered reaction polls have a narrow exception to the bot-message skip:
only Carl's verified numeric author ID and recognized consecutive 1–10 choice
format are accepted. The question and answers are translated together, without
the asker username; Python restores choice emojis and a link to vote on the
original. Companions are informational, not separate polls. Ordinary reactions
on them are never mirrored to the original (a bot reaction would be a bot vote).
Channel enablement/languages and S&S queue priority still apply. Polls are never
grouped; unchanged question/options reuse the existing companion across restarts,
while text edits update it and original deletion removes it. Vote/reaction-only
updates do not trigger inference. Country-flag requests use the same safe poll
rendering, and 🚩/🔄 can target the original or companion. This is support for
Carl's numbered text polls, not arbitrary bot embeds or native Discord polls.

The primary S&S server (`DISCORD_GUILD_ID`) has priority over other servers in
the single-GPU queue, including edits, regeneration and requested translations.
Already-running generation is not interrupted; equal-priority work remains FIFO.
Consecutive messages from one person in one channel stay in a rolling burst until
15 seconds of inactivity (`BURST_WINDOW_SECONDS` in `.env`). A burst can extend
even after its translation is complete: the bot posts a new localized placeholder
at the end, removes its older translation, and updates the new post with the full
accumulated translation. A replacement is posted successfully before old webhook
messages are removed. Fast arrivals are collected after a 0.6-second pause, with
up to 3 seconds of batching before inference starts (`BATCH_PAUSE_SECONDS` and
`BATCH_MAX_SECONDS` in `.env`). If another source arrives
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
Every source message retains its display boundary, even if it ends in emoji or
is an unfinished sentence fragment. Existing newlines are not doubled. Multiline
text uses one full-context structured request with an ordered field per line;
Python reconstructs the original boundaries, blank lines and indentation rather
than asking inference to reproduce newline placement. All fragments still share
grammatical context; they are never translated through independent per-line calls.
Missing/extra fields or embedded generated newlines fail closed. Flagged feedback retains
the original per-message boundaries. Run `scripts/check_context.py` for live local
model checks of split sentences, slang, negation, and multiline formatting.
Editing an original updates its existing webhook translation in place, including
after a bot restart. This applies to messages translated with the webhook format.
If an edited original has no stored translation link, the bot creates a translation
when its current text, user mode and channel settings qualify. Existing source
groups are retained; later edits update the newly linked translation in place.
Verified successful translations have persistent snapshots. For long multiline
edits, only the changed block and neighboring lines are regenerated with context;
other translated lines are preserved exactly. Changed language/model/glossary,
participant changes, uncertain alignment, user flags or externally modified
webhook text force a full translation. Historical messages without a snapshot
need one full translation to establish their baseline. Single-paragraph text
and multi-target output currently use full translation rather than guessing
sentence alignment.
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
   For recovery before sign-in after a reboot, open PowerShell **as Administrator
   under the translator account** and run `./scripts/install-boot-tasks.ps1`.
   This updates the two existing tasks (no duplicate bot): translator/Ollama after
   a 45-second boot delay, maintenance after 90 seconds plus its five-minute timer.
   The bot and preflight use limited S4U user sessions, without storing a Windows password, and
   ignore overlapping starts. The bot waits for network availability and retries
   failed launches every minute. Both launch without console windows; startup
   errors are retained in `logs/startup.log`. The model remains in the same user's
   local Ollama library. An on-demand **Discord Translator Maintenance Reviewer**
   task uses the signed-in user's limited Interactive token for sandbox command
   execution. Before sign-in, translation and cheap checks run normally, but AI
   reviews wait for sign-in. The reviewer has no timer or stored password.
   A live bot session is not restarted by installation.
   Windows cannot switch on a physically powered-off PC: automatic power return
   also requires the BIOS/UEFI power-loss recovery setting or suitable UPS support.

Administrator is not needed. **Manage Messages** is needed to remove someone
else's country-flag/report reaction after delivery or review. Manage Webhooks lets the bot
create one translation webhook per channel and post with the sender's name/avatar.

## Commands

Music-bot command messages (for example `-p song`, `-fp song`, `-pf song`, `-play song`, `-skip`,
`-queue`) and slash-command text such as `/play song` are ignored before
posting a placeholder or calling the translation model. This applies in all
channels; ordinary chat in music channels and sentences mentioning commands
are still translated. Original command messages are never changed or deleted.
The command list covers Lara's published playlist, filter, music, settings,
and information commands from its [bot listing](https://top.gg/bot/944016826751389717)
and [website](https://laramusic.app/index.html), plus common music aliases.

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
Protected-syntax, line-layout, explicit participant-role and reference-overlap checks can trigger a retry;
they share a maximum of two model calls total. Successful first attempts remain
one call. Language, nonempty-output and truncation checks remain. Reports remain available
for further review, and replaying them does not alter their stored originals.
Literal `~` and `~~` delimiters are protected and must remain in order; a tilde
is never a paragraph separator. The translated message must retain the source's
newline count. Single inline tildes use schema-constrained JSON translation fields
in one full-context inference. The bot restores exact separator spacing and
position itself, rather than trusting model punctuation or newline counts.

Unicode emoji (including variation selectors, skin tones and joined sequences)
are protected syntax too: an expression cannot silently become another emoji.
Foreign quotations do not override the language of a substantive unquoted reply
in English/Russian auto mode. Standalone stretched `ого` is rendered as `wow`,
distinct from a stretched `о` sound. Explicit trailing artist-title music credits
may remain unchanged without causing a false wrong-language rejection; this
exception requires a music cue and an exact unchanged credit, and does not exempt
the surrounding prose from validation. These safeguards add no successful-path
model calls; damaged emoji share the existing two-call formatting retry budget.
Inline slash/dash command tokens are also preserved as identifiers. Discussion
of `/fix` or `-fix` remains translatable; actual command-only posts stay excluded.
If a command/mention is damaged, the existing second attempt uses fixed syntax
segments rather than asking the model to reposition that syntax in prose.
This fixed-field second attempt now applies to any protected syntax, including
standalone mentions, URLs and code. Successful first attempts still make one
request, and the total generation budget remains two calls.
URL protection requires a word boundary, and `www.` must be followed by a
hostname character. Stretched interjections such as `Owww...` and `Awww...` are
ordinary text, not partial links. Real links remain protected. Narration guidance
keeps each clause's grammatical experiencer rather than letting a nearby `your`
reference switch the entire passage; unusual wording is not silently rewritten
into a guessed body part. Semantic checks remain diagnostics, not guarantees.

Automatic translations, edit/delete rebuilds, retries and country-flag requests
also use bounded conversation context: up to three cached human originals from
the same channel in the preceding thirty minutes, plus a cached/resolved reply
target, with a shared 800-character source-text budget and four originals maximum.
Grouped bursts retain reply references from all their messages; reply targets
take priority. Whole messages only;
commands, webhooks, bots, current burst sources and future messages are excluded.
Speaker IDs/names distinguish participants without guessing gender. Context is
marked as coming from the current speaker or another speaker. Replies to our
webhook translations resolve back to cached human originals via stored links,
never to generated translation text; this uses the same bounds and no fetches.
Context is untrusted reference data, never additional text to translate. There are no history
fetches, no extra model calls, no larger model context window, and no persistent
context transcript. Cold starts/uncached replies fall back to available context.
The thirty-minute lookback accommodates slower replies without changing the
four-original / 800-character budget or adding any history fetches.
Verbose outputs that copy reference text beyond the current source are rejected.
On the ordinary plain-text path, the existing second attempt drops context rather
than repeating it; the two-call budget is unchanged. This is a conservative
overlap/length guard, not a complete semantic detector or quality guarantee.
Additional prompt tokens have a small but hardware-dependent inference cost.
The common translation prompt is compact (about 2,300 characters); detailed
narrative instructions are included only for matching source text. Source-matched
slang/glossary hints and all existing output checks remain enabled. Ollama prefill
batches are limited to 128 to reduce scratch-memory pressure on the desktop GPU.
The model, 3,072-token context window and two-request validation budget are unchanged.
Background recovery defers while the translation slot is busy without spending a
retry attempt. Logs record model request time, load/prompt/generation durations,
token counts and live-batch queue wait, never source text or credentials.
Measured request timings are not an end-to-end latency guarantee: queueing,
longer messages, multiple target languages and validation retries add time.
Validated per-language results have an in-memory LRU cache: 128 entries maximum,
five-minute reuse lifetime, keyed by exact source, language pair, relevant bounded
conversation context, model/endpoint, source-matching semantic rule revisions and
only the glossary entries actually used. Code patches/restarts do not flush or
globally change cache identity. A semantic fix bumps only its affected rule or
invalidates exact reported sources. Identical concurrent requests share one
generation, including its failure; failures and empty results are never cached.
New reports invalidate every context/target for the affected exact source text,
not unrelated results. The reported multiline form and inference-joined form
are both invalidated. In-flight affected results cannot repopulate the cache.
A second SQLite cache next to
the settings database (`translator.cache.sqlite3` by default) survives restarts:
retained validated results with a monitored 100,000-entry soft capacity target.
There is no age-based expiry or automatic persistent-result eviction. Legacy
expiry timestamps are ignored without rewriting or deleting existing entries.
Only hashed identities and translated outputs are stored, not raw source/context
keys. Outputs may still contain private conversation text: keep this local file
private like the settings database. Never cache errors or fuzzy/near matches.
Only a closed vocabulary of common EN/RU greetings and acknowledgements, plus
explicit RU-to-EN wellbeing questions, can be reused across unrelated history
changes. The reverse English question still retains context for ты/вы choices.
Grammatical completeness does not prove
context-independent meaning: other eligible sentences retain conversation and
parent-passage scope in their cache keys. Long paragraphs are split at conservative
sentence boundaries with exact whitespace separators restored in code. Only new
eligible fields are generated; all source fields remain shared grammatical context.
Incomplete clauses, unresolved references and English you (Russian ты/вы ambiguity)
remain context-sensitive. Cache hits at this level appear as fragment reuse in
`/tr-status`. Fully cached units can be assembled before joining the GPU queue.
Live generated-result LRU timestamps are touched at most once per minute per
entry, avoiding a SQLite write on every hit. Age does not invalidate results;
revision checks still use a fresh database snapshot. Standalone `ResultCache`
callers may explicitly opt into TTL/LRU for isolated uses, but the bot does not.
Exact/envelope approved-memory candidates
are checked in one SQL snapshot, without caching negative results or revisions.
Related request/fragment observations share a transaction. Diagnostic observations
remain separately bounded; persistent translations are not pruned on writes.
None of these changes expands linguistic reuse scope.
Standalone EN/RU greetings, acknowledgements and completion phrases also reuse
their exact lexical text across punctuation/emoji/tilde variants. Their original
decoration and line spacing are reattached in code; arbitrary clauses, names,
pronouns and language pairs are not normalized this way. A flag invalidates the
shared phrase scope, including in-flight writes, not the entire database.
Multi-line fields use the same lexical phrase key as standalone messages, rather
than storing only a punctuation-specific field. Emoji/tildes/spacing stay outside
that key. A known phrase field can be omitted from neural generation while the
remaining fields are translated jointly with the original source fields as
context. Ordinary clauses retain their external and parent context boundaries;
they are not globally cached as interchangeable word chunks. Phrase-write tokens
are captured before generation so a newly flagged phrase cannot be repopulated
by an old in-flight result. Partial reuse appears in fragment-hit counters;
it is not reported as a fully cache-served live delivery.
An unterminated phrase before another nonempty field stays in joint inference:
`No` followed by a noun on the next line is not assumed to be a standalone
negative answer. This boundary check also applies when retaining reviewed units.

Dependency checks distinguish referents, unfinished clauses, register and gender
requirements. They are conservative grammar checks, not a claim to solve all
coreference. Edited passages expand across connected lines rather than always
using one neighbor; unresolved context is retained and an overly large edit
falls back to full translation. Fully standalone edited rows can refresh alone.
Markdown emphasis/spoiler delimiters and whitespace in fixed-slot requests are
owned by the renderer: the model returns prose fields, not reconstructed markup.
All fields are translated together to preserve cross-line grammar and context.
Persistent cache reads remain usable when another SQLite writer temporarily
blocks optional LRU bookkeeping. Patches retain unaffected existing entries.
Standalone stretched greetings are rendered locally without inference (not
arbitrary spelling normalization). Conventional greetings/thanks/yes/no/okay,
including emoji decorations, can reuse exact results across unrelated history.
Human-reviewed corrections retain only safe independent whole/line units; a
contextual correction is never silently made global. Refreshing an edited report
also invalidates its old spelling, not just its new source text.
Cache miss metrics distinguish unseen sources, other context/settings keys,
expiry, report invalidation, rejected cached output, and storage failures.
Fragment skips distinguish grammar/context from protected syntax. Count local
renderings separately from cache hits; cache-fill misses are not a model-call
count. Synthetic replay hit rates do not estimate live hit rates: report actual
repetition opportunities in saved source data as well. Never relax referent
dependencies merely to improve hit percentages.
Regeneration and review invalidate the
affected source in both layers; persistent per-source revisions prevent stale
cross-process writes/hits. Full clearing remains available for administrative use.
Automatic and on-demand exact hits bypass the GPU queue. Cache-hit/sharing logs
contain no message text. Warmup always verifies the real model, never the cache.
Discord publication/repair-marker work releases the GPU slot first; source,
edit-version, deletion and setting checks still guard publication. Retry success
means a delivered result, not just successful inference. Missing webhook/message
destinations stop their retries without being reported as successful; transient
HTTP failures retain bounded retries.
Each automatic batch logs receipt-to-publication time, placeholder, collection,
GPU queue and translation-stage timings. Separate Discord delivery logs include
duration/outcome, and model logs report inference durations. Translation-stage
time includes validation and any preparation; it is not pure GPU kernel time.
Every five minutes, process-lifetime cache metrics report fast-lookup hits/misses,
per-language memory/disk hits, generation misses and shared in-flight work.
Fast-lookup and per-language hit percentages have separate denominators; do not
add them together. Source IDs are logged for correlation, never message content.
`run.py` holds an OS-level lock in ignored `data/translator.instance.lock` for
the entire bot lifetime. A second launch exits before model warmup or Discord
login; a crash releases the lock automatically even though the file remains.
This protects manual launches as well as overlapping scheduler launches.

The boot maintenance gate hands actual reviews to the on-demand signed-in user
task, preserving the same workspace-write sandbox and existing network policy.
The installed Windows runner was verified in the user session; its pipe startup
failed under the S4U/session-zero token. A zero CLI exit status alone is not a
completed review: the gate also requires successful command evidence and fresh
attempt receipts matching the captured report fingerprints. Failed or incomplete
runs use the existing bounded retry/backoff instead of suppressing untouched flags.

Use `/tr-status` for an ephemeral processing report: this server's average,
median and P95 automatic delivery time, sample count, average stages, outcomes,
and host-wide GPU queue/cache counters. Each server retains its last 50 automatic
live request outcomes with no time cutoff; other servers cannot evict its samples.
The report shows the actual sample count out of 50 and survives restarts in a
separate `.metrics.sqlite3` database next to the settings database. Monotonic
host cache counters accumulate per-process deltas without double-counting status
queries. Entry counts and GPU queue state remain current gauges, not cumulative
totals. Counters are saved after deliveries and at periodic metric snapshots;
an abrupt power loss can lose increments since the last save. No conversation
text is retained. Initial historical recovery uses only available real log
records: final logged process counter snapshots can omit later increments, and
older isolated delivery-stage times are reconstructed from rounded stage totals.
Status persistence is optional: an unreadable statistics database cannot block
translator startup. Invalid saved samples are skipped without deleting the file
or other valid samples. `/tr-status` explicitly labels process-only counters and
warns when history persistence is unavailable or incomplete. Runtime counter
writes can recover on a later successful snapshot without double-counting;
startup storage failures use RAM until restart after the storage issue is fixed.
A grouped burst counts as one request, not one sample per constituent message.
Short multi-sentence messages also expose complete closed-vocabulary phrases
(for example, a standalone thanks) as cache fields. Remaining fields are still
translated jointly, with all original fields supplied as context. This does not
make arbitrary clauses portable or approve a generated fragment. Literal source
separators and affected-source invalidation remain unchanged.
Only successful automatic live batches enter delivery averages; manual requests,
background retries and regeneration are excluded. Empty windows show no average,
not a misleading zero. Small-sample percentiles are provisional. Host-wide cache
and queue figures are explicitly labeled and contain no conversation text.
The common instruction prefix is stable across language directions for KV reuse;
fixed syntax/blank fields are restored locally instead of generated. Prefill batch
size is configurable via `OLLAMA_NUM_BATCH` (64/128/256/512); 128 remains the
measured best warm-cache choice on this machine. Flash Attention is already auto.
Queued edits of the same original are coalesced to the latest revision; deleted
or newly disabled queued work is skipped. Group bursts still use their full
latest source text and retain the existing in-flight version checks. Work that
has already reached the model is not forcibly cancelled, but obsolete outputs
are not published. These changes reduce duplicate work, not model accuracy.
An affirmative Russian response without question wording cannot become an
English auxiliary-led question; a detected change uses the existing retry.
Quoted spelling alternatives with two similar capitalized identifiers have no
translation when the model retains both names exactly and the question count.
This narrow no-prose case suppresses unnecessary failure posts, without
exempting ordinary unchanged sentences from target-language validation.
Identity questions using Russian про кого/о кого retain who/whom rather than
becoming yes/no questions. Source-matched glossary guidance distinguishes calf
muscles from caviar, directional hugs from actions while held, named role titles
from abstract qualities, and negated availability from affirmative availability.
Camel-case compound identifiers stay fixed through inference. Recognizable EN/RU
compound display names are then rendered using trusted component mappings (e.g.
ChaosGoblin ↔ ХаосГоблин, LadyGros ↔ ЛедиГрос); every component must be recognized.
Unknown/ambiguous compounds, tagged handles, actual Discord mentions, code, URLs,
emoji names and rank codes remain unchanged. Common personal names receive
conventional spelling guidance inside the existing request, with grammatical
inflection left to the model (John → Джон/Джоном, not the different name Иван).
Known standalone names can be rendered locally without model inference.
The EN/RU spelling/component tables are in `translator_bot/name_rendering.py`;
changes require a bot restart; name-rule fixes must invalidate affected names, not
unrelated cached conversation. This is
a conservative supported-name policy, not unrestricted name guessing. Other
language pairs retain the model's conventional-name guidance and fixed handles.
These are bounded safeguards and contextual hints, not a general quality guarantee;
suspected informal typos still require review against the actual conversation.
`scripts/correct_feedback.py withdraw-identifiers IDS` removes only failed
posts for this case after reproducing the unchanged result and verifying originals.
Narrow RU-to-EN checks reject demonstrable explicit body-owner swaps and aligned
first-person action spans that switch to another actor. Plain-text failures reuse
the existing second attempt, not an extra call. Mixed or unspecified owners are
not guessed. Generic versus directly addressed pronouns remain a semantic
ambiguity: prompt guidance helps but does not guarantee correctness.
Run `scripts/check_conversation.py` for synthetic semantic regressions and paired
warm-model timing; unit tests alone do not prove translation quality. Manual
slash-command text has no surrounding-channel context because it is not a message.
Messages with internal blank lines use the same structured path; blank fields
are fixed by the schema, including whitespace-only lines. Translated fields
cannot introduce newline characters. Fields cannot disappear, become empty, or move protected
tokens to another field. `scripts/check_layout.py` runs live local regressions.
Structured output uses standard JSON string escaping rather than regex-based
string grammar. Invalid JSON, field boundaries, protected tokens, or substantially
truncated individual fields get one full-context retry (two calls maximum).
No broken JSON is repaired speculatively; invalid retries are never published.
If an ordinary plain-text translation changes the newline count, its second
attempt uses the same full-context structured layout. It cannot make a third
request; successful first attempts stay unchanged.
If protected syntax is damaged in structured output, the second attempt keeps
syntax and spacing in fixed fields while translating all prose segments together.
The same second-attempt fallback handles plain-text translations that lose
bold/italic delimiters. Successful first attempts still use just one request.
Outer single square brackets are restored outside inference. Alliance rank codes
R1–R5 (including Cyrillic р) are normalized and protected rather than interpreted
as prose. English output containing untranslated multiword Russian prose is
rejected; explicit quoted source phrases and names/initialisms are not blanket-banned.
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
🔄 before removing 🚩. The bot's reaction is a visible repair indicator.
Add your own 🔄 to a tracked translation or its original to regenerate in place
using up to eight prior originals / 1,200 characters (normal: four / 800).
Manual regeneration fetches up to 20 earlier channel messages and bounded direct
reply targets, retaining at most eight human originals / 1,200 characters. It
audits the previous wording in the same translation call, without a separate
review-model call. Ordinary automatic translation remains cache-only. ⏳ marks
an active regeneration. A correct translation may remain unchanged.
Requests bypass the result cache and have a
30-second per-group cooldown. Bot reactions never trigger regeneration or mirror
onto the original. The requesting user's 🔄 is removed after an admitted request,
so they can request another regeneration later. Existing 🚩 reports stay open;
a regenerated output is not automatically a reviewed correction. Failed generation
leaves the old translation intact. Permission failures are retried;
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
translation-result cache in that replay tool; the running bot's persistent cache
is invalidated by new reports and the correction apply/withdraw workflow. Review
does not reset server preferences, message links, or downloaded model files.

## Translation review procedure

### Pipeline work measurements

Known deterministic chat renderings are checked before persistent-cache reads
and the GPU queue, including their first use. Whole-message purr sounds preserve
letter count, case, punctuation and emoji through direct script rendering; names,
handles, commands and mixed prose remain outside that rule. `/tr-status` separates
local fast-path origins from memory, SQLite and assembled-cache origins. A local
rendering is not a stored translation-cache hit.

The local model's reported native cached prompt tokens, prefill time, generation
time and prompt size are retained as hash-only request audit metadata and logged
without prompt text. Missing `prompt_eval_cached_count` is **unavailable**, not
zero. `/tr-status` reports these independently from translation-result cache hits.
The exact context-safety guidance and unchanged human reference block precede
dynamic direction, line counts and source-specific guidance. Consecutive target
languages can therefore share that reference prefix without dropping context or
fidelity rules. A shorter common prompt was tested but not enabled because it
mostly removed tokens already cached by the runtime.

Incremental edit reuse now considers aligned messages from 80 characters and
three lines, not only long documents. It still refreshes grammatical neighbors,
expands dependencies, rejects changed participant anchors or incompatible policy,
and declines if the changed block exceeds 55 percent. The bot checks current live
output/flags before reusing a snapshot. Reused characters are accounted for in
delivery metrics; they are not a measured latency improvement. Full translation
remains the fallback. No arbitrary cross-conversation word substitution is added.

Maintenance runtime note: the reviewer resolves its Codex executable at each due
launch. A still-existing explicit path is preferred, then the current native CLI
on PATH, then the newest installed per-user app bundle. A removed versioned bundle
path is not a permanent dependency. `data/maintenance/reviewer_runtime.json`
records actual running/completed/failed reviewer status separately from the
Windows handoff request. Completion still requires command events and fresh
fingerprint-bound receipts; a successful scheduled-task launch alone is not review.

Reviews aim for close, faithful translation, not word-for-word calques or a more
polished rewrite. Preserve explicit actors, possession, actions, tense, negation,
modality, intensity, specific pet names, metaphors and formatting. Context may
resolve an ambiguous sense or omitted referent; it must not supply a different
action, beneficiary, gender, motive or emotional interpretation. A conventional
phrase may require a grammatical equivalent, but added meaning must be identified.

1. **Capture current evidence.** Read the live original messages, their exact
   grouping, current webhook output and direct reply. Fetch only relevant human
   surrounding messages. Generated translations are not evidence for gender or
   intent. Record source and context message IDs privately under ignored `data/`.
2. **Read the source before judging fluency.** Divide it into clauses and state
   each clause's actor, action, object, ownership, time, negation, modality and
   tone. Mark genuine omissions and typos separately. A suspected verb typo is
   not permission to replace the verb with a different plausible verb.
3. **Align source and candidate.** For every clause, record what is preserved,
   added, omitted or changed, including swearing and intensifiers. A translation
   can sound natural and still change the subject or lose a word's meaning.
4. **Classify the finding.** Use `confirmed_error`, `acceptable_variant`,
   `uncertain_source`, or `no_error`. Only confirmed errors justify an automatic
   corrective edit. Leave acceptable wording alone. Seek clarification for a
   material ambiguity; an explicit user retirement closes a report without
   certifying its translation as correct.
5. **Cross-check honestly.** Reconstruct the meaning in a separate source-first
   pass. For disputed senses use an authoritative dictionary, explicit author
   clarification, or a genuinely independent local reference where available.
   Another output from the same model and back-translation are diagnostics, not
   independent proof. Google screenshots are references, not an oracle. Never
   send private chat to an external translator without explicit authorization.
6. **Approve a minimal correction.** Explain why each changed clause is wrong
   and how the replacement preserves its meaning. Record the exact source and
   candidate hashes, clause alignment, context evidence, remaining uncertainty
   and reference method. A candidate from `stage` stays open until `approve`.
   Do not bypass approval through SQL status changes.
7. **Check the wider fix.** Test a synthetic example of the error, a literal
   counterexample where the rule must not fire, context variation, and source
   edits/deletions as applicable. Prefer general linguistic rules to memorizing
   private sentences. Grammar independence is not proof of context-independent
   word sense. Do not loosen cache reuse simply to increase its hit percentage.
8. **Handle cache scope.** Identify whole-message and derived-unit entries for
   affected sources. Invalidate those scopes across targets/contexts, including
   in-flight writes. Retain unaffected entries and revisions. Only portable,
   unambiguous approved units may be shared across unrelated conversations.
   Never seed the cache from a merely generated or disputed candidate.
9. **Publish and verify separately.** Recheck the source and reviewed context
   before applying. Verify exact live webhook content, the regeneration marker,
   and report-flag removal. Preserve audit history. Source drift requires a new
   review, not applying the old candidate.
10. **Report four distinct results.** State linguistic findings and uncertainty;
    delivery and reaction verification; regression-test results; and measured
    cache/performance evidence. Tests and exact publication do not prove meaning.
    Give the audit sample size and distinguish saved snapshots, model replays,
    actual live requests and synthetic cache hits. Do not claim universal accuracy.

The operational sequence is `inspect_feedback.py` -> optional
`correct_feedback.py stage` -> clause review and private JSON evidence ->
`correct_feedback.py approve <ids> <evidence.json>` ->
`correct_feedback.py apply <ids>` -> live verification. Evidence files are keyed
by report ID and validated by `translator_bot/review_evidence.py`. Approval is an
explicit linguistic judgment; schema validation only checks that the required
evidence exists and still matches, not whether the judgment is correct.

Each record contains `decision: confirmed_error`, `candidate_text`, UTF-8
`source_sha256` / `candidate_sha256`, and exact `source_message_ids`. Its `clauses`
give exact `source_span` / `candidate_span` substrings and a specific `finding`
and `change_reason`, including clauses deliberately unchanged. `checks` explain
`actors`, `actions`, `ownership`, `tense_negation_modality`, `tone_intensity`,
`lexical_specificity` and `formatting`; bare pass/fail labels are insufficient.
`uncertainty` records unresolved meaning. `reference_check` names the method,
finding and an honest boolean `independent`. `context` contains human `message_id`
and content `sha256` records, rechecked before apply. The synthetic record in
`tests/test_review_evidence.py` is a complete example.

Staging uses no persistent result cache and leaves reports open. Approval
receipts are append-only in local `feedback_review_evidence`; `apply` requires
a matching receipt. Private receipt JSON and audit exports stay under ignored
`data/`, not in public documentation or test fixtures.

For an investigated report requiring **no corrective edit**, use
`scripts/resolve_feedback.py <ids> data/resolutions.json`. Each private record
binds `report_fingerprint` to the current report and gives a specific `reason`.
`acceptable_variant` / `no_error` require the same source-first clause alignment,
checks, exact current candidate/source hashes, source IDs and human-context hashes
as a correction review; they close as `closed_no_change`, not `applied`.
`obsolete` requires `missing_translation_id` and a fresh verified HTTP 404 for
that exact reported post. A missing link alone is insufficient. Uncertain sources
stay open unless explicitly retired by the user through a separate review.

The resolver rechecks live evidence, retains the full report and resolution in
`feedback_resolutions`, removes only the reporting user's flags from originals
and the reported post, and verifies their absence. It never edits translations,
adds a regeneration marker, creates a correction approval, or seeds cache entries.
A cleanup failure leaves a durable `cleanup_verified=0` receipt; inspect it and
retry with a fresh report fingerprint. Reflagging later starts a new report while
retaining the old history. These closures must not be reported as corrected posts.

Approved portable phrases now retain SQLite provenance. Later generation cannot
overwrite a retained reviewed entry, including across processes; a targeted
flag still invalidates it normally. Approved multi-line reviews learn the same
decoration-free phrase keys used by live layout inference and lookups. Existing
unreviewed entries are not silently promoted. `/tr-status` distinguishes generated
unit writes, approved writes and retained approved rows from actual cache hits;
these counters are not an accuracy score. Reviews must never make contextual prose
portable simply to improve the hit percentage.

### Explicit translation memory and cache diagnostics

`scripts/translation_memory.py approve data/memory_review.json` accepts a list of
**separately reviewed reuse approvals**. It calls no model and does not edit
Discord. A correction approval, no-change closure, repetition count or fluent
generated output is never sufficient to approve memory scope.

Each record includes `decision: translation_memory`, exact `source_text` and
`candidate_text` with their hashes, directional `source_language` /
`target_language`, clause alignment, all linguistic checks, uncertainty and an
honest reference check. Also require `scope_kind`, `decoration`, a specific
`scope_reason` and at least two meaningful `counterexamples`. Record the current
`invalidation_revision` (global and exact fragment-source revision) before review;
a flag during the review rejects stale approval. Keep evidence in ignored data/.

- `portable`: explicitly assert `context_independent: true`. No particular human
  context may be necessary. English-you register and underspecified English
  first-person past gender cannot be approved globally. This extends the closed
  built-in phrase vocabulary without editing code for every new phrase.
- `dependency_block`: retain the exact `conversation_context` JSON and reviewed
  `parent_text`. The whole containing paragraph, including lexical topic and
  connected grammatical dependencies, defines the smaller parent scope. A unique
  occurrence is required. Full external human context still matches exactly;
  changed register/referents or uncertain/repeated boundaries miss safely. This
  scope is used only for explicitly reviewed units, never arbitrary prose merely
  classified as grammatically independent. Outside-parent independence requires
  its own linguistic justification and counterexamples.
- `decoration: exact` preserves exact source spelling/punctuation. The optional
  `literal_envelope` contract approves undecorated lexical cores only. Code may
  reattach the original whole-phrase bold/italic/underline/spoiler/strike wrappers,
  punctuation, emoji, whitespace and literal tilde. Partial markup, code, URLs,
  mentions, multi-line constructions and broken-sentence boundaries do not qualify.

Chat-based reviews must provide allowed guild/channel IDs, current source IDs and
hash-bound human context. The CLI verifies them before retaining an approval.
Pure lexical/dictionary reviews use `reference_check.kind:
source_first_lexical_review` with empty source/context ID lists; never invent
Discord IDs for a standalone lexical example. Schema validation checks evidence
completeness, not semantic truth. Preserve ambiguity rather than approving it.

Approvals are appended to `memory_approvals` in the private cache database; active
entries live in `translation_memory`, separately from retained generated results.
They are still bound to language direction, model/settings/glossary, source-scoped
semantic revisions and per-source invalidation. A flag revokes affected approved
units/variants across processes, while retaining receipts and unrelated data.
Regeneration bypasses this tier. Existing generated or reviewed result rows are
not automatically promoted into durable or portable memory.

`scripts/cache_report.py` is a cheap **read-only** report of integrity, retained
memory and hashed inference-path fragment observations. Repeated misses distinguish
structural eligibility from changed context/parent identities; neither proves safe
reuse. Diagnostics contain no source/context plaintext, are capped at 10,000 groups,
and cannot approve translations. Review actual repeated opportunities before
extending reuse; do not increase cache size or remove context solely for hit rate.

The report also separates request-aware observations by automatic delivery work,
retry, regeneration and manual origin. Source identity and source-version hashes
separate edits and repeated attempts from distinct user messages. Only automatic
observations from distinct message identities count as live repeated-unit
opportunities; these remain inference observations, not completed-delivery hits.
Fixed punctuation, laughter and emoji-only fields are excluded. Older mixed-attempt
observations remain labelled as legacy rather than being retroactively reclassified.
Both diagnostic tables are bounded independently; neither approves reusable scope.

Missing sentence-final punctuation is no longer itself an `unfinished` diagnosis.
Trailing function words, continuation clauses, ellipses, dangling separators and
unbalanced brackets still block grammatical-fragment reuse. Additional explicit
English copular/auxiliary clauses can be stored, but retain exact external and
parent context. Grammatical eligibility never grants cross-conversation portability.

The recent cache-decisions section of `/tr-status` is a separate SQLite-backed
sample of the latest 50 automatic source versions since diagnostics v2 deployment.
It combines probe/inference phases, excludes manual/retry/regeneration work and
deduplicates repeated attempts. A whole-result cold miss means no retained source
scope at that lookup, not a failed translation or proof of missed partial reuse.
Partial inference, cache-only results and overlapping blockers are reported
separately. These are host-wide inference decisions, not the server delivery sample.
Unreused repeated phrase candidates still require scope/alignment review; they are
not counted as proven safe opportunities or measured speed savings.

Russian inflected referents retain context just as nominative pronouns do. A
smaller dependency paragraph is usable only through a separate exact scope approval;
the application never promotes a repeated fragment or guessed antecedent on its own.
Balanced Markdown emphasis newly introduced around prose words can be unwrapped
without another model call. Author markup remains code-owned; unmatched symbols,
numeric expressions, changed protected tokens and collapsed lines still fail guards.

`/tr-status` separates full-cache deliveries, partial-cache deliveries and source
characters reused across targets in the last 50 outcomes. Only successful
deliveries contribute to reuse totals. These are characters not regenerated as
translation output, **not tokens or a measured time-saving estimate**: joint model
requests still see fixed source fields for grammatical context. Host lookup/write
counters include manual activity and are not a live delivery percentage. Cheap
scheduled health reports retain the same distinctions without waking AI when idle.

### Auditable lexical and template reuse

The shared reuse planner applies existing approved memory to short sentences and
formatted prose as well as ordinary layout fields. It retains unresolved fields
in a single joint model request, with the original source fields as context.
Fixed translations are not human context evidence. Fully covered messages skip
inference; partially covered messages omit only fixed fields from generation.
First-time or ambiguous language still needs inference. A partial hit is not a
promise of reduced prompt processing or a measured time saving.

Additional rules use `scripts/translation_memory.py approve data/review.json`
and the same complete source-first memory evidence. Set `scope_kind: portable`,
`decoration: exact` and a separate `rule_kind` / `usage`:

- `rule_kind: lexical`, `usage: field`: the exact word/phrase is reusable only
  as a complete field. It does not authorize replacing that word in a sentence.
- `rule_kind: lexical`, `usage: embedded_invariant`: additionally requires
  `target_form_invariant: true` and a substantive `embedding_reason` explaining
  lexical sense, grammatical form, token boundaries and counterexamples.
  Only exact approved forms match; no automatic stemming, lowercasing or guessed
  inflection. Longest phrases win; conflicting approvals fail closed. Code,
  URLs, Discord tokens and compounds cannot supply partial word matches.
- `rule_kind: template`, `usage: field`: add `source_template`,
  `target_template`, `slots` and a substantive `slot_reason`. Both templates
  must instantiate the concrete reviewed `source_text` / `candidate_text`.
  Named slots occur once in each template and retain their literal value.
  Slots allow ASCII `identifier` codes containing a digit, or `enum` with an
  explicit bounded list of ASCII codes/numbers. They never accept free prose,
  arbitrary regex, Python expressions, unbounded numbers or inferred grammar.
  Negation, punctuation, direction and fixed wording must match exactly.

For a synthetic structural example (not production approval), a template may
bind `Server {code} is online.` to `Сервер {code} в сети.` with
`slots: {"code": {"type": "identifier"}}`; the concrete review must cover an
actual instantiation and justify all allowed slot values and counterexamples.
See `tests/test_reuse_planner.py` for complete synthetic evidence fixtures.
Do not promote these fixtures or generated candidates into production memory.

`scripts/translation_memory.py prepare data/input.json --output data/draft.json`
fills only exact hashes and current invalidation revisions using read-only SQL.
It grants no approval, fills no linguistic judgments and never overwrites input.
Complete the independent source-first review before calling `approve`.
Rules retain append-only private receipts, exact direction, model/settings and
relevant glossary/rule identity. Explicit regeneration bypasses memory rules.
Targeted flags revoke affected rules/template families and their derived result
entries across processes; in-flight writes cannot restore revoked approvals.
Unrelated results and approval history are retained. No global cache reset occurs.
To retire a reviewed rule without editing Discord, use
`scripts/translation_memory.py revoke data/revocations.json` with a list of
`{"receipt": <active rule receipt>, "reason": "specific private explanation"}`.
It retains a revocation receipt and invalidates only that scope and its derived
results. Reinspect and prepare fresh evidence before replacing a retired rule.

Run `scripts/reuse_report.py --output data/reuse-report.json` for bounded,
hash-only observations. Optional `--include-text` maps only exact saved source
versions and requires a private output path under ignored `data/`. Reports cover
at most 2,000 probe/translation events, separate automatic/retry/manual origins,
record actual per-request model calls/tokens, identify repeated words, ngrams,
fields and identifier/number template candidates, and associate exact-version
flags with used approval receipts. Flags are associations, not causal proof or
an accuracy score. Candidate repetition and character coverage never approve
meaning or establish time saved. Probe and inference events are not deliveries.
`/tr-status` continues to use successful automatic deliveries for live reuse;
lexical/template lookup counters are separately labelled host diagnostics.

The native daily health check saves `data/maintenance/reuse_patterns.json`
without inference or new AI wakeups. During an already-due review, examine
relevant recurring candidates, review at most five well-supported scopes with
counterexamples, and retain uncertain candidates unchanged. Compare subsequent
live model calls, delivery latency, failures and linked flags before expanding
reuse. No automatic self-training, fuzzy substitution or bulk promotion occurs.

### Agreement-based review scheduling

The persistent generated-result soft capacity target is **100,000 entries**, with
no age expiry or automatic eviction. The process-local LRU remains bounded
separately; its short-lived entries can be reloaded from retained SQLite results,
so RAM demotion does not discard stored translations. Increasing disk
capacity does not load 100,000 translations into RAM. Existing entries are kept.
The native maintenance gate records counts, utilization and database size in
ignored `data/maintenance/cache_capacity.json` on each preflight. Stable alerts
at 80%, 90% and 100% request capacity review without starting AI for every added
row. Review disk space and invalidated data before deciding on targeted cleanup
or capacity expansion; never delete known translations merely because of age.

`scripts/memory_candidates.py --output data/concordance.json` cheaply compares
up to 5,000 recent saved source/target snapshots. It reports distinct-source
support, dominant exact lexical output, alternative outputs, agreement percentage,
reported-source exclusions and alignment uncertainty. Duplicate reporters,
repeated units within a source and retries do not create extra source votes.
These are saved snapshots, not independent model experiments or an accuracy rate.

The private report also mines bounded within-field two-to-four-word phrases and
construction skeletons. Ngrams never cross sentence/newline/protected boundaries;
they do not guess a target substring from whole-sentence output. Source-only
phrases remain manual alignment/scope candidates. Exact one-line bilingual
identifier constructions can be nominated for the same bounded scheduled review
when three distinct sources, at least two literal codes and a single fixed target
construction agree. Conflicts and flagged sources suppress nomination. A nominee
is not an approval: the reviewer must justify every slot and exact construction,
then use the normal evidence/approve gate. No arbitrary names, free prose slots,
inferred morphology or percentage-based auto-approval is introduced.
Only whole-line lexical envelopes are compared; no substring alignment is guessed.

The existing native gate refreshes these statistics once daily. **At least three
distinct supporting source IDs and >=95% agreement nominate a review; they never
approve reuse.** Already-approved scopes and any historically reported sources
are excluded from unattended nomination. At most five new scopes per rolling day
are supplied to the existing signed-in reviewer, even when no flags are due.
Idle or unchanged queues still use no AI; the separate chat heartbeat stays paused.

The reviewer performs source-first lexical scope review with references and
counterexamples, then uses the existing approval gate. Candidate outcomes are
recorded through `scripts/memory_candidates.py --record` with their captured
fingerprint and a specific private reason. `approved` additionally verifies an
active matching receipt; rejection/deferral cannot approve anything. These attempt
receipts are distinct from linguistic approval and Discord flag resolution.
Unchanged rejected/deferred scopes stay suppressed when only their frequency
changes. Fresh matching attempt receipts are required for job completion.

## Scheduled maintenance

The native Windows task **Discord Translator Maintenance Gate** runs a small
Python preflight every five minutes. **Idle checks use no AI tokens:** the
database threshold is evaluated before starting Codex. The old chat heartbeat is
paused to avoid replaying the entire conversation on every poll. Keep the machine
on; actual AI reviews require the Windows user to be signed in (the desktop may
be locked). The gate uses the installed, authenticated
Codex CLI; the desktop app need not repeatedly wake this chat.

Install/update the native task with `scripts/install-maintenance-task.ps1`.
It starts `pythonw.exe` directly through `scripts/maintenance_hidden.py`, so no
console is created (rather than hiding PowerShell after its window opens).
It uses limited user privileges and ignores overlapping starts. Output and
background failures still go to `data/maintenance/gate.log`.
`scripts/run_maintenance.py` also holds an OS lock for the entire reviewer run.
Only one scheduler should be enabled; do not re-enable the old heartbeat alongside
the native task. Disable the native task in Windows Task Scheduler to pause it.

`scripts/maintenance_queue.py` reads the settings database in SQLite read-only
mode without loading Ollama. It emits IDs and hashes, not private message text.
Review becomes due at **three distinct flagged translation posts** across allowed
servers; multiple reporters on one post count once. Smaller queues become due
after 24 hours. S&S is first. A maintenance run reviews at most ten posts and
follows the evidence/approval/publication procedure above. It does not blindly
accept a regenerated candidate or clear unresolved flags.

To inspect the gate without launching AI:

```powershell
.\.venv\Scripts\python.exe scripts/run_maintenance.py --dry-run
```

This does not translate, approve or change Discord. `maintenance_queue.py --force`
still lists suppressed/pending reports without reviewing them. To manually launch
a reviewer regardless of the threshold/backoff, run `run_maintenance.py --force`.
No new Discord slash command is registered by this scheduler.

When due, the gate launches a fresh, bounded `codex exec` session with the saved
`docs/maintenance-reviewer.md` workflow, queue IDs/fingerprints and health alerts.
It does **not** resume or replay this long chat. The CLI uses its configured model
and existing ChatGPT login; no new API subscription/key is installed. It runs in
workspace-write sandbox mode with network access for Discord, no interactive
approvals, and no sandbox bypass. Steps requiring unavailable authority must stop
and be reported instead of widening permissions.

Private run evidence, events and short results are saved under
`data/maintenance/runs/`. `dispatcher_status.json` records the latest preflight;
`dispatcher_state.json` prevents a completed, unchanged batch from relaunching.
Failed batches back off for 30 minutes and stop after three attempts for the same
fingerprint; new work or an explicit `--force` can launch again. These are dispatch
receipts, not linguistic approvals. Results are local files, not idle chat posts.

After actually inspecting a report, maintenance records the inspected fingerprint
using `--record <ids> --outcome <outcome> --fingerprints <JSON>`.
The JSON maps report IDs to the fingerprints from the pre-review queue output.
Outcomes are `corrected_verified`, `needs_clarification`, `acceptable_variant`,
or `unavailable`. Receipts live under ignored `data/maintenance/`; they suppress
repeat work on unchanged reports but do not close reports or approve translations.
Changed report contents/status or a new report become eligible again; `--force`
also revisits suppressed reports. Source/context drift still requires live review.

The native preflight performs a cheap health/cache/delivery inspection once per
24 hours, with its last completed check in `data/maintenance/health_state.json`.
Healthy daily inspections do not start AI either. A cache integrity failure,
three recent failed deliveries or three exhausted automatic retries can dispatch
an investigation. It avoids heavy GPU replays while live translation is busy.
Unaffected cache entries must survive maintenance. Code changes require
regression tests before any translator restart. Only actual reviewer runs use
the account's model allowance; the bot does not host the Codex reviewer.

## Local operation

The internet is needed during initial package/model installation and for the
ongoing Discord connection. Message text is processed by Ollama on `127.0.0.1`
and is not sent to a translation service. If the quality model is unavailable,
the bot fails closed instead of silently using a weaker or remote translator.

Run tests with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
