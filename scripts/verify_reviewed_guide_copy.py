"""Verify only explicitly approved guide copies, including attachment bytes."""
import hashlib
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
import requests

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from translator_bot.config import Settings
from translator_bot.storage import SettingsStore
from copy_alliance_guides import STATE, SOURCE, TARGET

def main():
    state=json.loads(STATE.read_text(encoding='utf-8'))
    approved=set(state['approved_guide_ids'])
    assert not state['pending'] and set(state['messages']) == approved
    settings=Settings.load()
    session=requests.Session(); session.headers['Authorization']='Bot '+settings.discord_token
    def api(method,path,**kwargs):
        for _ in range(6):
            try:
                r=session.request(method,'https://discord.com/api/v10'+path,timeout=30,**kwargs)
            except requests.RequestException:
                raise RuntimeError('Discord request failed; private URLs suppressed') from None
            if r.status_code==429:
                delay=float(r.json().get('retry_after',1))
                if delay>60: raise RuntimeError('Rate limited')
                time.sleep(delay+.2); continue
            if r.status_code>=400: raise RuntimeError(f'Discord HTTP {r.status_code}')
            return r.json() if r.content else {}
        raise RuntimeError('Rate limit persists')
    def history(channel):
        rows=[]; before=None
        while True:
            page=api('GET',f'/channels/{channel}/messages',params={'limit':100,**({'before':before} if before else {})})
            rows.extend(page)
            if len(page)<100: return rows
            before=page[-1]['id']
    source_channels=api('GET',f'/guilds/{SOURCE}/channels')
    source_order=sorted([c for c in source_channels if c['id'] in state['channels']],key=lambda c:c['position'])
    api('PATCH',f'/guilds/{TARGET}/channels',json=[{'id':state['channels'][c['id']],'position':i} for i,c in enumerate(source_order)])
    asset_jobs=[]; missed_pins=[]
    for c in source_order:
        destination=state['channels'][c['id']]
        channel=api('GET','/channels/'+destination)
        assert channel['guild_id']==TARGET and channel['parent_id']==state['category']
        assert channel['name']==c['name']
        rows=history(destination)
        owned={m['id']:m for m in rows if m.get('webhook_id')==state['webhooks'][c['id']]}
        expected={state['messages'][m['id']] for m in state['snapshot'][c['id']] if m['id'] in approved}
        assert set(owned)==expected, 'Unapproved or missing copied message'
        for original in state['snapshot'][c['id']]:
            if original['id'] not in approved: continue
            actual=owned[state['messages'][original['id']]]
            assert actual.get('content','')==original.get('content','')
            assert not any(e.get('footer',{}).get('text','').startswith('S&S archive · ') for e in actual.get('embeds',[]))
            assert [a['filename'] for a in actual.get('attachments',[])]==[a['filename'] for a in original.get('attachments',[])]
            for i,a in enumerate(actual.get('attachments',[])):
                local=STATE.parent/'guide-assets'/(original['id']+'-'+str(i)+'-'+a['filename'])
                assert local.exists(), 'Expected reviewed attachment missing locally'
                asset_jobs.append((a['url'],local))
            if original.get('pinned') and not actual.get('pinned'):
                missed_pins.append(actual['id'])
        print('VERIFIED',channel['name'],len(owned),'posts',flush=True)
    def verify_asset(job):
        url,local=job
        assert urlparse(url).hostname in ('cdn.discordapp.com','media.discordapp.net')
        try:
            r=requests.get(url,timeout=30); r.raise_for_status()
        except requests.RequestException:
            raise RuntimeError('Attachment verification failed; private URL suppressed') from None
        assert hashlib.sha256(r.content).digest()==hashlib.sha256(local.read_bytes()).digest(), 'Attachment byte mismatch'
        return len(r.content)
    with ThreadPoolExecutor(max_workers=3) as executor:
        byte_count=sum(executor.map(verify_asset,asset_jobs))
    store=SettingsStore(settings.database_path,channel_defaults={g:g not in settings.default_off_guild_ids for g in settings.guild_ids})
    assert all(store.is_channel_enabled(int(TARGET),int(c)) for c in state['channels'].values())
    with store._lock:
        languages=store._connection.execute('SELECT languages FROM global_language_settings WHERE guild_id=?',(int(TARGET),)).fetchone()
    assert languages and json.loads(languages[0])==['en','ru']
    state['pins_not_restored']=missed_pins
    state['reviewed_verification']={'posts':len(approved),'attachments':len(asset_jobs),'bytes':byte_count,'excluded_posts':sum(map(len,state['snapshot'].values()))-len(approved),'unrestored_pins':len(missed_pins),'default_languages':['en','ru'],'translation_enabled':True}
    temp=STATE.with_suffix('.tmp'); temp.write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8'); temp.replace(STATE)
    print('COMPLETE',state['reviewed_verification'],flush=True)

if __name__=='__main__': main()
