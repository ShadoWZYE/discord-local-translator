"""Approve exact private translation-memory evidence, never generated candidates.

Usage: translation_memory.py approve data/memory_review.json
Records are a JSON list. Standalone lexical reviews have no invented Discord IDs;
reviews tied to chat must verify live human source/context before local approval.
No Discord edits or model calls occur here.
"""
import json
import argparse
from contextlib import closing
import sqlite3
from pathlib import Path
import sys
import requests

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from translator_bot.config import Settings
from translator_bot.translation import LocalTranslator
from translator_bot.review_evidence import text_hash
from translator_bot.reuse_planner import rule_scope
from translator_bot.result_cache import ResultCache


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('approve','prepare','revoke'))
    parser.add_argument('path',type=Path)
    parser.add_argument('--output',type=Path,help='Separate private draft; prepare never approves or changes the input.')
    args=parser.parse_args()
    path=args.path.resolve()
    if not path.is_relative_to(ROOT/'data'):raise ValueError('Evidence belongs in ignored data/.')
    records=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(records,list):raise ValueError('Expected a list of separately reviewed units.')
    settings=Settings.load()
    if args.action=='prepare':
        output=args.output.resolve() if args.output else None
        if output is None or not output.is_relative_to(ROOT/'data') or output==path:
            raise ValueError('Prepare needs a separate --output under ignored data/.')
        with closing(sqlite3.connect(settings.database_path.with_suffix('.cache.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
            for record in records:
                scope=rule_scope(record) if record.get('rule_kind') else LocalTranslator._source_key('fragment:'+record['source_text'])
                record['invalidation_revision']=list(ResultCache._revision(db,scope))
                record['source_sha256']=text_hash(record['source_text'])
                record['candidate_sha256']=text_hash(record['candidate_text'])
        output.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
        print('PRIVATE DRAFT PREPARED; no linguistic approval; review every field before approve')
        return
    translator=LocalTranslator(settings.ollama_url,settings.ollama_model,settings.ollama_timeout_seconds,
        cache_path=settings.database_path.with_suffix('.cache.sqlite3'))
    if args.action=='revoke':
        for record in records:
            translator._reuse_store().revoke(record['receipt'],record['reason'])
            print('REUSE RULE REVOKED',record['receipt'],'no Discord posts edited')
        return
    with requests.Session() as session:
        session.headers['Authorization']='Bot '+settings.discord_token
        def human(channel,ident):
            try:response=session.get(f'https://discord.com/api/v10/channels/{channel}/messages/{ident}',timeout=20)
            except requests.RequestException:raise RuntimeError('Discord read failed; URL suppressed.') from None
            if response.status_code!=200:raise RuntimeError(f'Live evidence HTTP {response.status_code}; retry review later.')
            result=response.json()
            if result.get('webhook_id') or result['author'].get('bot'):raise ValueError('Human evidence required.')
            return result['content']
        for index,record in enumerate(records):
            if record.get('source_message_ids'):
                if record.get('guild_id') not in settings.guild_ids:raise ValueError('Out-of-scope guild.')
                channel=record['channel_id']
                source='\n'.join(human(channel,i) for i in record['source_message_ids'])
                if record['source_text'] not in source:raise ValueError('Reviewed unit not in current live source.')
                if record['scope_kind']=='dependency_block' and source!=record['parent_text']:
                    raise ValueError('Reviewed dependency parent differs from the current live grouping.')
                for context in record['context']:
                    if text_hash(human(channel,context['message_id']))!=context['sha256']:
                        raise ValueError('Reviewed human context changed.')
            receipt=(translator.approve_reuse_rule(record) if record.get('rule_kind') else
                translator.approve_translation_memory(record['source_text'],record['source_language'],
                    record['target_language'],record['candidate_text'],record))
            print('MEMORY SCOPE APPROVED',index,'receipt',receipt,record['scope_kind'],flush=True)


if __name__=='__main__':main()
