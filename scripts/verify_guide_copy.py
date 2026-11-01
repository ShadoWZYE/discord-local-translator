"""Verify the specific guide archive, repair internal links, check file hashes."""
import copy
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
import requests

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from translator_bot.config import Settings
from copy_alliance_guides import STATE, SOURCE, TARGET

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    state=json.loads(STATE.read_text(encoding='utf-8'))
    if state.get('privacy_review_required'):
        raise RuntimeError('Broad archive verification is disabled after privacy cleanup; use the reviewed-guide workflow.')
    expected=sum(len(rows) for rows in state['snapshot'].values())
    if state['pending'] or len(state['messages'])!=expected:
        raise RuntimeError('Migration not finished; verification cannot confirm completion.')
    session=requests.Session(); session.headers['Authorization']='Bot '+Settings.load().discord_token
    def api(method,path,**kwargs):
        for attempt in range(6):
            try: response=session.request(method,'https://discord.com/api/v10'+path,timeout=60,**kwargs)
            except requests.RequestException: raise RuntimeError('Discord request failed; private URLs suppressed.') from None
            if response.status_code!=429: break
            delay=float(response.json().get('retry_after',1))
            if delay>60 or attempt==5: raise RuntimeError('Rate limit persists.')
            time.sleep(delay+.2)
        if response.status_code>=400: raise RuntimeError(f'Discord HTTP {response.status_code}; private URLs suppressed.')
        return response.json() if response.content else None
    def rewrite(text):
        if not isinstance(text,str): return text
        for old,new in state['channels'].items():
            text=text.replace('<#'+old+'>','<#'+new+'>')
            pattern=r'https://(?:discord.com|discordapp.com)/channels/'+SOURCE+'/'+old+r'/(\d+)'
            text=re.sub(pattern,lambda m:'https://discord.com/channels/'+TARGET+'/'+new+'/'+state['messages'][m.group(1)] if m.group(1) in state['messages'] else m.group(),text)
            text=text.replace('https://discord.com/channels/'+SOURCE+'/'+old,'https://discord.com/channels/'+TARGET+'/'+new)
        return text
    def recursive(value):
        if isinstance(value,dict): return {key:recursive(item) for key,item in value.items()}
        if isinstance(value,list): return [recursive(item) for item in value]
        return rewrite(value)
    def digest(url):
        if urlparse(url).hostname not in ('cdn.discordapp.com','media.discordapp.net'): raise RuntimeError('Unexpected file host.')
        try:
            result=requests.get(url,timeout=60); result.raise_for_status()
        except requests.RequestException: raise RuntimeError('Attachment verification failed; private URL suppressed.') from None
        return hashlib.sha256(result.content).digest()
    total_files=0
    for old,new in state['channels'].items():
        posts={}; before=None
        while True:
            page=api('GET',f'/channels/{new}/messages',params={'limit':100,**({'before':before} if before else {})})
            posts.update({post['id']:post for post in page})
            if len(page)<100: break
            before=page[-1]['id']
        hook=api('GET','/webhooks/'+state['webhooks'][old])
        for original in state['snapshot'][old]:
            post=posts.get(state['messages'][original['id']])
            if not post or post.get('webhook_id')!=hook['id']: raise RuntimeError('Missing or unowned archive post.')
            embeds=recursive([{key:copy.deepcopy(embed[key]) for key in ('title','description','url','timestamp','color','footer','image','thumbnail','author','fields') if key in embed} for embed in post.get('embeds',[])])
            desired=rewrite(original.get('content',''))
            reply_id=original.get('message_reference',{}).get('message_id')
            if reply_id in state['messages']:
                reply_channel=next(cid for cid,rows in state['snapshot'].items() if any(m['id']==reply_id for m in rows))
                embeds[-1]['description']='[Reply to copied post](https://discord.com/channels/'+TARGET+'/'+state['channels'][reply_channel]+'/'+state['messages'][reply_id]+')'
            if desired!=post['content'] or embeds!=[{key:copy.deepcopy(embed[key]) for key in ('title','description','url','timestamp','color','footer','image','thumbnail','author','fields') if key in embed} for embed in post.get('embeds',[])]:
                post=api('PATCH',f'/webhooks/{hook["id"]}/{hook["token"]}/messages/{post["id"]}',json={'content':desired,'embeds':embeds,'allowed_mentions':{'parse':[]}})
            if post['content']!=desired: raise RuntimeError('Archived text mismatch.')
            if len(post.get('attachments',[]))!=len(original.get('attachments',[])): raise RuntimeError('Missing attachment.')
            for source_file,destination_file in zip(original.get('attachments',[]),post.get('attachments',[])):
                if source_file['filename']!=destination_file['filename'] or digest(source_file['url'])!=digest(destination_file['url']):
                    raise RuntimeError('Copied attachment bytes differ.')
                total_files+=1
            if original.get('pinned') and not post.get('pinned'):
                api('PUT',f'/channels/{new}/pins/{post["id"]}')
        print('VERIFIED',new,len(state['snapshot'][old]),'posts',flush=True)
    destination=api('GET',f'/guilds/{TARGET}/channels')
    positions=[{'id':state['channels'][old],'position':index} for index,old in enumerate(state['channels'])]
    api('PATCH',f'/guilds/{TARGET}/channels',json=positions)
    if not all(any(c['id']==new and c.get('parent_id')==state['category'] for c in destination) for new in state['channels'].values()):
        raise RuntimeError('Category placement mismatch.')
    print('VERIFIED TOTAL',expected,'posts;',total_files,'attachments with identical SHA-256 hashes',flush=True)

if __name__=='__main__': main()
