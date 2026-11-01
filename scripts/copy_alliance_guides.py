"""Explicit, resumable S&S guide archive. Runtime state is private/ignored.

Without --apply: read-only inventory and attachment checks.
With --apply: create a NEW category/channels and webhook archive; never delete
or mutate source messages. Credentials are never stored in migration state.
"""
import argparse
import copy
import json
import sys
import time
import struct
import zlib
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from translator_bot.config import Settings

SOURCE = '1554947765489107135'
CATEGORY = '1557011853203349524'
TARGET = '1558860759570579508'
STATE = Path(__file__).resolve().parent.parent / 'data' / ('guide-copy-' + TARGET + '.json')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    apply = '--apply' in sys.argv
    reviewed = '--reviewed' in sys.argv
    session = requests.Session()
    session.headers['Authorization'] = 'Bot ' + Settings.load().discord_token

    def api(method, path, **kwargs):
        for attempt in range(6):
            try:
                response = session.request(method, 'https://discord.com/api/v10' + path, timeout=60, **kwargs)
            except requests.RequestException:
                raise RuntimeError('Discord request failed; credential URLs suppressed. Resume only after checking the pending archive marker.') from None
            if response.status_code != 429:
                break
            delay = float(response.json().get('retry_after', 1))
            if delay > 60 or attempt == 5:
                raise RuntimeError('Discord rate limit persists; resume later.')
            time.sleep(delay + .2)
        if response.status_code >= 400:
            raise RuntimeError(f'Discord HTTP {response.status_code}; no credential URLs printed.')
        return response.json() if response.content else None

    def save():
        STATE.parent.mkdir(exist_ok=True)
        temporary = STATE.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        temporary.replace(STATE)

    def messages(channel):
        result, before = [], None
        while True:
            page = api('GET', f'/channels/{channel}/messages', params={'limit': 100, **({'before': before} if before else {})})
            result.extend(page)
            if len(page) < 100:
                return sorted(result, key=lambda m: int(m['id']))
            before = page[-1]['id']

    source_channels = api('GET', f'/guilds/{SOURCE}/channels')
    category = next(c for c in source_channels if c['id'] == CATEGORY)
    channels = sorted([c for c in source_channels if c.get('parent_id') == CATEGORY], key=lambda c: c['position'])
    target_channels = api('GET', f'/guilds/{TARGET}/channels')
    guild = api('GET', f'/guilds/{TARGET}')
    if any(c['type'] != 0 for c in channels):
        raise RuntimeError('Unexpected non-text channel; explicit handling required.')
    state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {'category': None, 'channels': {}, 'webhooks': {}, 'messages': {}, 'snapshot': {}, 'pending': None}
    if apply and state.get('privacy_review_required') and not reviewed:
        raise RuntimeError('Broad archive is locked after privacy cleanup. Only explicitly reviewed guide posts may be copied.')
    if reviewed and not state.get('approved_guide_ids'):
        raise RuntimeError('Reviewed mode requires an explicit approved guide manifest.')
    if not state['category'] and any(c['name'] == category['name'] and c['type'] == 4 for c in target_channels):
        raise RuntimeError('Untracked category already exists; do not create duplicates.')
    active = api('GET', f'/guilds/{SOURCE}/threads/active')['threads']
    if any(t.get('parent_id') in {c['id'] for c in channels} for t in active):
        raise RuntimeError('Source has active threads; thread migration requires separate handling.')
    for channel in channels:
        archived = api('GET', f'/channels/{channel["id"]}/threads/archived/public', params={'limit': 100})
        if archived.get('threads'):
            raise RuntimeError('Source has archived threads; thread migration requires separate handling.')
        if channel['id'] not in state['snapshot']:
            state['snapshot'][channel['id']] = [m for m in messages(channel['id'])
                if m.get('type', 0) in (0, 19, 20) and (m.get('content') or m.get('attachments') or m.get('embeds'))]
        rows = state['snapshot'][channel['id']]
        print('INVENTORY', channel['name'], len(rows), 'posts', sum(len(m.get('attachments', [])) for m in rows), 'attachments', flush=True)
    attachments = [a for rows in state['snapshot'].values() for m in rows for a in m.get('attachments', [])]
    max_size = guild.get('max_file_size', 10 * 1024 * 1024)
    if any(a['size'] > max_size for a in attachments):
        raise RuntimeError('An attachment exceeds the destination upload limit; no new copy was started.')
    if any(len(m.get('embeds', [])) >= 10 for rows in state['snapshot'].values() for m in rows):
        raise RuntimeError('A post has no room for its provenance marker; needs separate handling.')
    print('TOTAL', sum(len(rows) for rows in state['snapshot'].values()), 'posts;', len(attachments), 'attachments;', sum(a['size'] for a in attachments), 'bytes', flush=True)
    if not apply:
        return
    save()
    if not state['category']:
        # Preserve @everyone privacy/write restrictions, not invalid foreign
        # role IDs. Administrators retain access in the new server.
        overwrites = [dict(o, id=TARGET) for o in category.get('permission_overwrites', []) if o['id'] == SOURCE]
        if any(int(o['deny']) & 1024 for o in overwrites):
            me = api('GET', '/users/@me')
            overwrites.append({'id': me['id'], 'type': 1, 'allow': str(1024+2048+65536+536870912+8192), 'deny': '0'})
        created = api('POST', f'/guilds/{TARGET}/channels', json={'name': category['name'], 'type': 4, 'permission_overwrites': overwrites})
        state['category'] = created['id']; save()
    for channel in channels:
        source_id = channel['id']
        if source_id not in state['channels']:
            body = {'name': channel['name'], 'type': 0, 'parent_id': state['category'], 'topic': channel.get('topic'), 'nsfw': channel.get('nsfw', False), 'rate_limit_per_user': channel.get('rate_limit_per_user', 0)}
            created = api('POST', f'/guilds/{TARGET}/channels', json=body)
            state['channels'][source_id] = created['id']; save()
        destination = state['channels'][source_id]
        if source_id not in state['webhooks']:
            hook = api('POST', f'/channels/{destination}/webhooks', json={'name': 'S&S guide archive'})
            state['webhooks'][source_id] = hook['id']; save()
        hook = api('GET', '/webhooks/' + state['webhooks'][source_id])
        for message in state['snapshot'][source_id]:
            if reviewed and message['id'] not in state['approved_guide_ids']:
                continue
            marker = 'S&S archive · ' + message['id']
            if message['id'] in state['messages']:
                continue
            # Recover an unknown outcome rather than blindly duplicating a post.
            if state['pending']:
                pending = state['pending']
                if pending.get('reviewed'):
                    found = [m for m in messages(pending['channel'])
                             if m.get('webhook_id') == pending['webhook']
                             and int(m['id']) > int(pending['after'])
                             and m.get('content','') == pending['content']
                             and [a['filename'] for a in m.get('attachments',[])] == pending['filenames']]
                else:
                    found = [m for m in messages(pending['channel']) if any(e.get('footer', {}).get('text') == pending['marker'] for e in m.get('embeds', []))]
                if len(found) != 1:
                    raise RuntimeError('Pending archive post needs manual review; not reposted automatically.')
                state['messages'][pending['source']] = found[0]['id']; state['pending'] = None; save()
                if message['id'] in state['messages']:
                    continue
            files = []
            for index, attachment in enumerate(message.get('attachments', [])):
                if urlparse(attachment['url']).hostname not in ('cdn.discordapp.com', 'media.discordapp.net'):
                    raise RuntimeError('Unexpected attachment host; no credentials sent.')
                # This unauthenticated request never sends the bot token to CDN.
                try:
                    downloaded = requests.get(attachment['url'], timeout=60)
                    downloaded.raise_for_status()
                except requests.RequestException:
                    raise RuntimeError('Attachment download failed; private URLs suppressed.') from None
                if len(downloaded.content) != attachment['size']:
                    # Discord's CDN can add PNG metadata after reporting the
                    # original upload size. Accept only a complete CRC-valid PNG
                    # with the original dimensions; never accept truncated data.
                    data = downloaded.content
                    position, valid, dimensions, kind = 8, data.startswith(b'\x89PNG\r\n\x1a\n'), None, None
                    while valid and position + 12 <= len(data):
                        length = struct.unpack('>I', data[position:position+4])[0]
                        end = position + 12 + length
                        if end > len(data):
                            valid = False; break
                        kind = data[position+4:position+8]
                        body = data[position+8:position+8+length]
                        crc = struct.unpack('>I', data[position+8+length:end])[0]
                        if zlib.crc32(kind+body) & 0xffffffff != crc:
                            valid = False; break
                        if kind == b'IHDR' and len(body) == 13:
                            dimensions = struct.unpack('>II', body[:8])
                        position = end
                        if kind == b'IEND':
                            valid = valid and end == len(data); break
                    if (not valid or position != len(data) or kind != b'IEND'
                            or dimensions != (attachment.get('width'), attachment.get('height'))
                            or len(data) > max_size):
                        raise RuntimeError('Attachment size mismatch; post not published.')
                    print('Verified CDN PNG metadata adjustment:', attachment['size'], 'to', len(data), 'bytes', flush=True)
                files.append((f'files[{index}]', (attachment['filename'], downloaded.content, attachment.get('content_type', 'application/octet-stream'))))
            embeds = [{k: copy.deepcopy(e[k]) for k in ('title','description','url','timestamp','color','footer','image','thumbnail','author','fields') if k in e}
                      for e in message.get('embeds', [])]
            for embed in embeds:
                for key in ('image','thumbnail'):
                    if key in embed:
                        embed[key] = {'url': embed[key]['url']}
                        for attachment in message.get('attachments', []):
                            if embed[key]['url'].split('?')[0] == attachment['url'].split('?')[0]:
                                embed[key]['url'] = 'attachment://' + attachment['filename']
            if not reviewed:
                embeds.append({'footer': {'text': marker}, 'timestamp': message['timestamp']})
            author = message['author']; username = (message.get('member', {}).get('nick') or author.get('global_name') or author['username'])[:80]
            payload = {'content': message.get('content', ''), 'username': username, 'embeds': embeds, 'allowed_mentions': {'parse': []},
                'attachments': [{'id': i, 'filename': a['filename'], **({'description': a['description']} if a.get('description') else {})} for i,a in enumerate(message.get('attachments', []))]}
            if author.get('avatar'):
                payload['avatar_url'] = f'https://cdn.discordapp.com/avatars/{author["id"]}/{author["avatar"]}.png'
            state['pending'] = {'source': message['id'], 'channel': destination, 'marker': marker}
            if reviewed:
                latest = api('GET', f'/channels/{destination}/messages', params={'limit':1})
                state['pending'].update(reviewed=True, webhook=hook['id'], after=latest[0]['id'] if latest else '0',
                                        content=payload['content'], filenames=[a['filename'] for a in message.get('attachments',[])])
            save()
            arguments = {'data': {'payload_json': json.dumps(payload, ensure_ascii=False)}, 'files': files} if files else {'json': payload}
            posted = api('POST', f'/webhooks/{hook["id"]}/{hook["token"]}', params={'wait': 'true'}, **arguments)
            if posted['content'] != payload['content'] or len(posted.get('attachments', [])) != len(files):
                raise RuntimeError('Archive response did not match the published post; state retained.')
            state['messages'][message['id']] = posted['id']; state['pending'] = None; save()
            if message.get('pinned'):
                try:
                    api('PUT', f'/channels/{destination}/pins/{posted["id"]}')
                except RuntimeError as exc:
                    if 'Discord HTTP 403;' not in str(exc):
                        raise
                    state.setdefault('pins_not_restored', []).append(posted['id']); save()
                    print('Copied guide retained; pin permission unavailable.', flush=True)
        print('COPIED', channel['name'], flush=True)
    print('COMPLETE', len(state['channels']), 'channels;', len(state['messages']), 'posts', flush=True)


if __name__ == '__main__':
    main()
