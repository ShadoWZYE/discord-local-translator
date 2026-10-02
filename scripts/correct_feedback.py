"""Stage explicit report corrections, then apply only human-reviewed candidates.

Usage: correct_feedback.py stage 23,24
       correct_feedback.py apply 23,24
       correct_feedback.py withdraw-no-text 43
Credentials never enter printed diagnostics. Applied records are retained; the
bot removes their report reactions with permission-aware, persistent cleanup.
"""
import sys
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8')
import requests
from translator_bot.config import Settings
from translator_bot.storage import SettingsStore
from translator_bot.translation import LocalTranslator
from translator_bot.translation import detect_multilingual
from translator_bot.languages import LANGUAGES
from translator_bot.bot import combined_message
from types import SimpleNamespace


def main():
    action, ids = sys.argv[1], [int(x) for x in sys.argv[2].split(',')]
    if action not in ('stage', 'apply', 'withdraw-no-text', 'refresh-source'):
        raise ValueError('Use stage, apply, withdraw-no-text, or refresh-source with explicit report IDs.')
    settings = Settings.load()
    store = SettingsStore(settings.database_path)
    session = requests.Session()
    session.headers['Authorization'] = 'Bot '+settings.discord_token
    translator = LocalTranslator(settings.ollama_url, settings.ollama_model, settings.ollama_timeout_seconds)

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
            if action=='refresh-source':
                if row['status']!='open':
                    raise RuntimeError('Only an open report can refresh its source snapshot.')
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
            if action=='withdraw-no-text' and (link['status']!='failed'
                    or detect_multilingual(context, tuple(LANGUAGES)) is not None):
                raise RuntimeError('Only failed posts with no translatable source text can be withdrawn.')
            if action=='stage':
                targets = row['target_language'].split(',')
                results = [translator.translate_to(context, target, row['source_language']) for target in targets]
                if any(r is None for r in results):
                    raise RuntimeError('No translated candidate; report left unresolved.')
                correction = results[0].text if len(results)==1 else '\n\n'.join(
                    f'**{LANGUAGES[r.target]}**\n{r.text}' for r in results)
                if len(correction)>1900:
                    raise RuntimeError('Candidate needs multiple chunks; report left unresolved.')
                with store._connection:
                    store._connection.execute("UPDATE translation_feedback SET status='reviewed',corrected_text=? WHERE id=?", (correction,id))
                print('CANDIDATE', id, correction, flush=True)
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
            if action=='withdraw-no-text':
                request('DELETE', f'/webhooks/{link["webhook_id"]}/{token}/messages/{post["id"]}{thread}')
                if request('GET', f'/channels/{channel_id}/messages/{post["id"]}', allow_missing=True) is not None:
                    raise RuntimeError('Withdrawn post still exists; review not marked complete.')
                store.unlink_translation(int(post['id']))
                store.cancel_retry(link['source_message_id'])
                with store._connection:
                    store._connection.execute("UPDATE translation_feedback SET status='applied',corrected_text='', "
                        "note=COALESCE(note,'') || ' Removed erroneous failure post: source has no translatable text.' "
                        "WHERE translation_message_id=? AND status IN ('open','reviewed')", (row['translation_message_id'],))
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
            for applied in store._connection.execute(
                    "SELECT id FROM translation_feedback WHERE translation_message_id=? AND status='applied'",
                    (row['translation_message_id'],)).fetchall():
                store.defer_flag_cleanup(applied['id'], 0)
            print('APPLIED AND VERIFIED', id, post['id'], flush=True)
    finally:
        store.close()
        session.close()


if __name__=='__main__':
    main()
