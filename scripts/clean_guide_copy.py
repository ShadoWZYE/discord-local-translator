"""Remove copied private chat, leaving source and new human posts untouched."""
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from translator_bot.config import Settings
from copy_alliance_guides import STATE, TARGET

# Explicitly reviewed standalone guide text. Everything else is held back,
# including screenshots not yet visually reviewed and translated private chat.
EXTRA_GUIDES = set('''1558159410344370209 1558159411934003324
1558186747039580291 1558186748167590010
1558405035925307433 1558405037577732118
1558758723713896569 1558758725915906099
1558758955268841475 1558758956858609746'''.split())


def main():
    state = json.loads(STATE.read_text(encoding='utf-8'))
    state['privacy_review_required'] = True
    def save():
        temp = STATE.with_suffix('.tmp')
        temp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        temp.replace(STATE)
    save()
    session = requests.Session()
    session.headers['Authorization'] = 'Bot ' + Settings.load().discord_token
    def api(method, path, **kwargs):
        for _ in range(6):
            try:
                response = session.request(method, 'https://discord.com/api/v10'+path, timeout=30, **kwargs)
            except requests.RequestException:
                raise RuntimeError('Discord request failed; URLs suppressed.') from None
            if response.status_code == 429:
                delay = float(response.json().get('retry_after', 1))
                if delay > 60:
                    raise RuntimeError('Rate limited; cleanup remains resumable.')
                time.sleep(delay+.2)
                continue
            if response.status_code == 404:
                return None
            if response.status_code >= 400:
                raise RuntimeError(f'Discord HTTP {response.status_code}; URLs suppressed.')
            return response.json() if response.content else {}
        raise RuntimeError('Rate limit persists.')
    def history(channel):
        rows, before = [], None
        while True:
            page = api('GET', f'/channels/{channel}/messages', params={'limit':100, **({'before':before} if before else {})})
            rows.extend(page)
            if len(page)<100:
                return rows
            before = page[-1]['id']
    for channel in state['channels'].values():
        actual = api('GET', '/channels/'+channel)
        assert actual['guild_id'] == TARGET, 'Cleanup must stay inside destination guild.'
    pending = state.get('pending')
    if pending:
        found = [m for m in history(pending['channel'])
                 if str(m.get('webhook_id')) in set(state['webhooks'].values())
                 and any(e.get('footer',{}).get('text') == pending['marker'] for e in m.get('embeds',[]))]
        if len(found)>1:
            raise RuntimeError('Ambiguous pending post; no deletion performed.')
        if found:
            state['messages'][pending['source']] = found[0]['id']
        state['pending'] = None
        save()
    approved = set(state['approved_guide_ids']) if state.get('approved_guide_ids') else (
        EXTRA_GUIDES | {m['id'] for m in state['snapshot']['1557010198961332234']
                       if int(m['id']) <= 1558114213384298547})
    state['approved_guide_ids'] = sorted(approved)
    save()
    removed, stripped = 0, 0
    for source, channel in state['channels'].items():
        hook = api('GET', '/webhooks/'+state['webhooks'][source])
        existing = {m['id']:m for m in history(channel)}
        unwanted = []
        for row in state['snapshot'][source]:
            copied_id = state['messages'].get(row['id'])
            if not copied_id:
                continue
            actual = existing.get(copied_id)
            if actual:
                assert actual.get('webhook_id') == hook['id'], 'Only migration-owned posts may be changed.'
            if row['id'] not in approved:
                unwanted.append((row['id'],copied_id))
        for offset in range(0,len(unwanted),100):
            batch = unwanted[offset:offset+100]
            present = [dest for _,dest in batch if dest in existing]
            if len(present)>1:
                api('POST', f'/channels/{channel}/messages/bulk-delete', json={'messages':present})
            elif present:
                api('DELETE',f'/channels/{channel}/messages/{present[0]}')
            for original,dest in batch:
                state.setdefault('removed_for_privacy',{})[original] = dest
                del state['messages'][original]
            removed += len(present)
            save()
        for original,dest in list(state['messages'].items()):
            actual = existing.get(dest)
            if not actual:
                continue
            embeds = [e for e in actual.get('embeds',[]) if e.get('footer',{}).get('text') != 'S&S archive · '+original]
            if len(embeds) != len(actual.get('embeds',[])):
                result = api('PATCH', f'/webhooks/{hook["id"]}/{hook["token"]}/messages/{dest}',
                             json={'embeds':embeds,'allowed_mentions':{'parse':[]}})
                assert result['content'] == actual['content']
                assert [a['id'] for a in result.get('attachments',[])] == [a['id'] for a in actual.get('attachments',[])]
                stripped += 1
        verified = history(channel)
        owned = [m for m in verified if m.get('webhook_id') == hook['id']]
        allowed_dest = set(state['messages'].values())
        assert all(m['id'] in allowed_dest for m in owned), 'Unexpected migration post remains.'
        assert not any(e.get('footer',{}).get('text','').startswith('S&S archive · ') for m in owned for e in m.get('embeds',[]))
        print('Verified cleaned channel',channel,'retained',len(owned),flush=True)
    print('COMPLETE removed',removed,'copied posts; removed archive cards',stripped,'; retained',len(state['messages']),flush=True)


if __name__ == '__main__':
    main()
