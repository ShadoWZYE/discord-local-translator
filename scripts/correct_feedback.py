"""Stage explicit report corrections, then apply only human-reviewed candidates.

Usage: correct_feedback.py stage 23,24
       correct_feedback.py approve 23,24 data/review_evidence.json
       correct_feedback.py apply 23,24
       correct_feedback.py withdraw-no-text 43
Credentials never enter printed diagnostics. Applied records are retained; the
bot removes their report reactions with permission-aware, persistent cleanup.
"""
import sys
import time
import json
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8')
import requests
from translator_bot.config import Settings
from translator_bot.storage import SettingsStore
from translator_bot.translation import LocalTranslator, TranslationUnavailable
from translator_bot.translation import detect_multilingual, is_bot_command, is_phonetic_sample
from translator_bot.languages import LANGUAGES
from translator_bot.carl_polls import parse_carl_poll
from translator_bot.bot import combined_message
from types import SimpleNamespace
from translator_bot.review_evidence import validate_evidence,save_approval,load_approval,text_hash


def main():
    action, ids = sys.argv[1], [int(x) for x in sys.argv[2].split(',')]
    if action not in ('stage', 'approve', 'apply', 'withdraw-no-text', 'withdraw-command', 'withdraw-identifiers', 'withdraw-phonetics', 'refresh-source'):
        raise ValueError('Use stage, apply, withdraw-no-text, withdraw-command, or refresh-source with explicit report IDs.')
    settings = Settings.load()
    store = SettingsStore(settings.database_path)
    session = requests.Session()
    session.headers['Authorization'] = 'Bot '+settings.discord_token
    translator = LocalTranslator(settings.ollama_url, settings.ollama_model, settings.ollama_timeout_seconds,
        cache_path=None if action=='stage' else settings.database_path.with_suffix('.cache.sqlite3'))
    records={}
    if action=='approve':
        evidence_path=Path(sys.argv[3]).resolve()
        if not evidence_path.is_relative_to(Path(__file__).resolve().parent.parent/'data'):
            raise ValueError('Keep private review evidence inside ignored data/.')
        records=json.loads(evidence_path.read_text(encoding='utf-8'))

    def request(method, path, *, allow_missing=False, **kwargs):
        for attempt in range(4):
            try:
                r = session.request(method, 'https://discord.com/api/v10'+path, timeout=30, **kwargs)
            except requests.RequestException:
                raise RuntimeError('Discord request failed; credential URLs suppressed.') from None
            if r.status_code!=429:
                break
            delay=float(r.json().get('retry_after',1))
            if delay>30 or attempt==3:
                raise RuntimeError('Discord rate limit persists; retry this operation later.')
            time.sleep(max(0,delay)+0.1)
        if r.status_code==404 and allow_missing:
            return None
        if r.status_code>=400:
            raise RuntimeError(f'Discord request returned HTTP {r.status_code}; credential URLs suppressed.')
        return r.json() if r.content else None

    try:
        for id in ids:
            row = store._connection.execute('SELECT * FROM translation_feedback WHERE id=?', (id,)).fetchone()
            if not row or row['guild_id'] not in settings.guild_ids:
                raise RuntimeError('Report missing or outside allowed servers.')
            if action=='apply' and (row['status']!='reviewed' or not row['corrected_text']):
                raise RuntimeError('Stage and inspect the candidate before applying.')
            channel_id = row['channel_id']
            link = store.get_translation_link(row['translation_message_id'])
            if not link or link['channel_id']!=channel_id:
                print('SKIPPED', id, 'translation no longer linked; report retained.')
                continue
            source_ids = store.get_batch_sources(link['source_message_id'])
            originals = [request('GET', f'/channels/{channel_id}/messages/{source_id}') for source_id in source_ids]
            if '\n'.join(m['content'] for m in originals)!=row['source_text'] and action!='refresh-source':
                print('SKIPPED', id, 'source changed since report; no stale correction published.')
                continue
            post = request('GET', f'/channels/{channel_id}/messages/{row["translation_message_id"]}')
            if int(post.get('webhook_id',0))!=link['webhook_id']:
                raise RuntimeError('Unexpected webhook ownership; no changes made.')
            if action in ('approve','apply'):
                record=(records.get(str(id)) if action=='approve' else
                        load_approval(store._connection,id,row['source_text'],row['corrected_text']))
                candidate=record.get('candidate_text','') if record else ''
                validate_evidence(row['source_text'],candidate,record)
                if set(map(int,record['source_message_ids']))!=set(source_ids):
                    raise RuntimeError('Reviewed source grouping changed; re-review before publication.')
                for evidence in record['context']:
                    original=request('GET',f'/channels/{channel_id}/messages/{evidence["message_id"]}')
                    if (original.get('webhook_id') or original['author'].get('bot')
                            or text_hash(original['content'])!=evidence['sha256']):
                        raise RuntimeError('Reviewed human context changed or is not a human original.')
                if action=='approve':
                    with store._connection:
                        save_approval(store._connection,id,row['source_text'],candidate,record)
                        store._connection.execute("UPDATE translation_feedback SET status='reviewed',corrected_text=? WHERE id=?",(candidate,id))
                    print('LINGUISTIC APPROVAL RECORDED',id,flush=True)
                    continue
            if action=='refresh-source':
                if row['status']!='open':
                    raise RuntimeError('Only an open report can refresh its source snapshot.')
                translator.invalidate_source(row['source_text'])
                with store._connection:
                    store._connection.execute('INSERT OR IGNORE INTO feedback_history VALUES (?,?)',
                        (row['id'],json.dumps(dict(row),ensure_ascii=False)))
                    store._connection.execute('UPDATE translation_feedback SET source_text=?,translated_text=?, '
                        "note=COALESCE(note,'') || ' Source edited; prior snapshot archived before new review.' WHERE id=?",
                        ('\n'.join(m['content'] for m in originals),post['content'],row['id']))
                print('REFRESHED SOURCE; PRIOR SNAPSHOT ARCHIVED',id,flush=True)
                continue
            context = combined_message([SimpleNamespace(id=int(m['id']), guild=None, channel=None,
                author=None, content=m['content']) for m in originals]).content
            poll=parse_carl_poll(SimpleNamespace(content=context,
                author=SimpleNamespace(**originals[0]['author']),webhook_id=int(originals[0]['webhook_id']) if originals[0].get('webhook_id') else None)) if len(originals)==1 else None
            if action == 'apply' or action.startswith('withdraw-'):
                translator.invalidate_source(row['source_text'])
                translator.invalidate_source(context)
                if poll:
                    translator.invalidate_source(poll.text)
            if action=='withdraw-no-text' and (link['status']!='failed'
                    or detect_multilingual(context, tuple(LANGUAGES)) is not None):
                raise RuntimeError('Only failed posts with no translatable source text can be withdrawn.')
            if action=='withdraw-command' and not all(is_bot_command(m['content']) for m in originals):
                raise RuntimeError('Only command-only groups can be withdrawn; ordinary chat must be preserved.')
            if action=='withdraw-phonetics' and not is_phonetic_sample(context):
                raise RuntimeError('Only nonlexical phonetic samples can be withdrawn.')
            if action=='withdraw-identifiers' and (link['status']!='failed'
                    or not translator._is_identifier_comparison(context)
                    or translator.translate_to(context, row['target_language'], row['source_language']) is not None):
                raise RuntimeError('Only failed, model-confirmed unchanged identifier comparisons can be withdrawn.')
            if action=='stage':
                targets = row['target_language'].split(',')
                results = [translator.translate_to(poll.text if poll else context, target, row['source_language']) for target in targets]
                if any(r is None for r in results):
                    raise RuntimeError('No translated candidate; report left unresolved.')
                correction = results[0].text if len(results)==1 else '\n\n'.join(
                    f'**{LANGUAGES[r.target]}**\n{r.text}' for r in results)
                if poll:
                    url=f'https://discord.com/channels/{row["guild_id"]}/{channel_id}/{link["source_message_id"]}'
                    correction='\n\n'.join(poll.render(r.text,r.target,url) for r in results)
                if len(correction)>1900:
                    raise RuntimeError('Candidate needs multiple chunks; report left unresolved.')
                with store._connection:
                    store._connection.execute("UPDATE translation_feedback SET status='open',corrected_text=? WHERE id=?", (correction,id))
                print('UNAPPROVED CANDIDATE; REVIEW REQUIRED', id, correction, flush=True)
                continue
            # Recheck original contents immediately before external mutation.
            current = [request('GET', f'/channels/{channel_id}/messages/{source_id}') for source_id in source_ids]
            if [m['content'] for m in current]!=[m['content'] for m in originals]:
                raise RuntimeError('Source changed during review; no stale correction published.')
            hook = request('GET', f'/webhooks/{link["webhook_id"]}')
            token = hook.get('token')
            if not token:
                raise RuntimeError('Original webhook token unavailable.')
            channel = request('GET', f'/channels/{channel_id}')
            thread = f'?thread_id={channel_id}' if channel['type'] in (10,11,12) else ''
            if action in ('withdraw-no-text', 'withdraw-command', 'withdraw-identifiers', 'withdraw-phonetics'):
                request('DELETE', f'/webhooks/{link["webhook_id"]}/{token}/messages/{post["id"]}{thread}')
                if request('GET', f'/channels/{channel_id}/messages/{post["id"]}', allow_missing=True) is not None:
                    raise RuntimeError('Withdrawn post still exists; review not marked complete.')
                store.unlink_translation(int(post['id']))
                store.cancel_retry(link['source_message_id'])
                reason = (' Removed erroneous translation: nonlexical phonetic sample should remain unchanged.'
                          if action=='withdraw-phonetics' else
                          ' Removed erroneous failure: identifier-only spelling comparison needs no translation.'
                          if action=='withdraw-identifiers' else
                          ' Removed erroneous translation: original is a bot command, not conversation.'
                          if action=='withdraw-command' else
                          ' Removed erroneous failure post: source has no translatable text.')
                # Flags on the deleted post disappear with it. Flags on surviving
                # originals are removed only after deletion has been verified.
                reports = store._connection.execute("SELECT id,reporter_id FROM translation_feedback WHERE "
                    "translation_message_id=? AND status IN ('open','reviewed')", (row['translation_message_id'],)).fetchall()
                for report in reports:
                    for flagged_id in store.feedback_flag_messages(report['id']):
                        if flagged_id != int(post['id']):
                            from urllib.parse import quote
                            request('DELETE', f'/channels/{channel_id}/messages/{flagged_id}/reactions/{quote("🚩", safe="")}/{report["reporter_id"]}', allow_missing=True)
                with store._connection:
                    store._connection.execute("UPDATE translation_feedback SET status='applied',corrected_text='', "
                        "note=COALESCE(note,'') || ? "
                        "WHERE translation_message_id=? AND status IN ('open','reviewed')", (reason,row['translation_message_id']))
                print('WITHDRAWN AND VERIFIED', id, post['id'], flush=True)
                continue
            result = request('PATCH', f'/webhooks/{link["webhook_id"]}/{token}/messages/{post["id"]}{thread}',
                json={'content':row['corrected_text'],'allowed_mentions':{'parse':[]}})
            if result['content']!=row['corrected_text']:
                raise RuntimeError('Published content did not match reviewed candidate.')
            store.link_translation(int(post['id']),link['source_message_id'],channel_id,link['webhook_id'],
                row['source_language'],row['target_language'],status='complete')
            store.cancel_retry(link['source_message_id'])
            with store._connection:
                store._connection.execute("UPDATE translation_feedback SET status='applied',corrected_text=? "
                    "WHERE translation_message_id=? AND status IN ('open','reviewed')", (row['corrected_text'],row['translation_message_id']))
            store.save_translation_snapshot(link['source_message_id'],context,row['source_language'],row['target_language'],row['corrected_text'],translator.policy_fingerprint())
            if poll:
                store.mark_poll(link['source_message_id'],poll.signature(
                    store.get_channel_languages(row['guild_id'],channel_id),translator.policy_fingerprint()))
            for applied in store._connection.execute(
                    "SELECT id FROM translation_feedback WHERE translation_message_id=? AND status='applied'",
                    (row['translation_message_id'],)).fetchall():
                store.defer_flag_cleanup(applied['id'], 0)
            if not poll and ',' not in row['target_language']:
                try:
                    retained=translator.remember_reviewed(context,row['source_language'],row['target_language'],row['corrected_text'])
                    print('APPROVED INDEPENDENT CACHE UNITS',id,retained,flush=True)
                except (OSError,sqlite3.Error,TranslationUnavailable) as exc:
                    # Cache retention must never block the already verified live
                    # correction or leave report reactions without cleanup jobs.
                    print('APPROVED CACHE NOT RETAINED',id,type(exc).__name__,flush=True)
            print('APPLIED AND VERIFIED', id, post['id'], flush=True)
    finally:
        store.close()
        session.close()


if __name__=='__main__':
    main()
