"""Cheap read-only pattern/audit report. No inference, approval or cache mutation."""
import argparse
from collections import Counter,defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from translator_bot.config import Settings
from translator_bot.reuse_planner import candidate_units


def report(cache_path,snapshots_path=None,*,include_text=False):
    with closing(sqlite3.connect(cache_path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result={'schema':'reuse-audit-v1','note':'Inference observations, NOT successful delivery rates or linguistic approval.',
                'retention_requests':2000,'active_rules':0,'approval_receipts':0,'by_origin_phase':[],'candidates':[]}
        if 'reuse_requests' not in tables:
            result['status']='not_deployed';return result
        result['status']='available'
        result['active_rules']=db.execute('SELECT COUNT(*) FROM reuse_rules').fetchone()[0]
        result['approval_receipts']=db.execute('SELECT COUNT(*) FROM reuse_approvals').fetchone()[0]
        result['revocation_receipts']=db.execute('SELECT COUNT(*) FROM reuse_revocations').fetchone()[0]
        rows=db.execute('SELECT message,version,origin,phase,payload,created FROM reuse_requests ORDER BY id').fetchall()
    summaries=defaultdict(Counter);candidates={};mapping={};feedback=defaultdict(set);quality={}
    if snapshots_path:
        with closing(sqlite3.connect(snapshots_path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if include_text and 'translation_snapshots' in names:
                for ident,text in db.execute('SELECT source_id,source_text FROM translation_snapshots'):
                    key=(hashlib.sha256(str(ident).encode()).digest(),hashlib.sha256(text.encode()).digest())
                    mapping[key]={hashlib.sha256(unit.encode()).hexdigest():unit for _,unit in candidate_units(text)}
            if 'translation_feedback' in names:
                for ident,text,post,status in db.execute('SELECT source_message_id,source_text,translation_message_id,status FROM translation_feedback'):
                    key=(hashlib.sha256(str(ident).encode()).digest(),hashlib.sha256(text.encode()).digest())
                    feedback[key].add((post,status))
    for message,version,origin,phase,raw,created in rows:
        payload=json.loads(raw);group=summaries[(origin,phase)]
        group['events']+=1;group['model_calls']+=payload['model_calls']
        group['model_wall_seconds']+=payload['model_wall_seconds']
        group['prompt_tokens']+=payload['prompt_tokens'];group['output_tokens']+=payload['output_tokens']
        group['failed_inference_events']+=payload['status']=='failed'
        group['zero_model_returned_events']+=bool(payload.get('returned_translation') and payload['model_calls']==0)
        used=set(payload.get('used_units',[]))
        if origin=='automatic' and payload.get('returned_translation') and payload['status']=='completed':
            for tier,receipt in payload['receipts']:
                item=quality.setdefault((tier,receipt),dict(tier=tier,receipt=receipt,sources=set(),flagged_posts=set()))
                item['sources'].add(message)
                item['flagged_posts'].update(feedback.get((message,version),()))
        for direction in payload.get('directions',[['unknown','unknown']]):
            for kind,fingerprint,characters in payload['candidates']:
                key=(kind,fingerprint,*direction)
                unit=candidates.setdefault(key,dict(kind=kind,unit_hash=fingerprint,source=direction[0],target=direction[1],
                    characters=characters,automatic_messages=set(),versions=set(),observed_reuse_messages=set(),other_origins=set()))
                if origin=='automatic':
                    unit['automatic_messages'].add(message);unit['versions'].add((message,version))
                    if fingerprint in used:unit['observed_reuse_messages'].add(message)
                else:unit['other_origins'].add(origin)
                if include_text and fingerprint in mapping.get((message,version),{}):unit['text']=mapping[(message,version)][fingerprint]
    for key,values in sorted(summaries.items()):
        result['by_origin_phase'].append(dict(origin=key[0],phase=key[1],**values))
    for values in candidates.values():
        for name in ('automatic_messages','versions','observed_reuse_messages'):
            values[name]=len(values[name])
        values['other_origins']=sorted(values['other_origins'])
        values['repeat_character_opportunity']=max(0,values['automatic_messages']-1)*values['characters']
        values['decision']='unreviewed_candidate'
    result['candidates']=sorted((v for v in candidates.values() if v['automatic_messages']>1),
        key=lambda v:(-v['repeat_character_opportunity'],v['unit_hash']))[:100]
    result['events_retained']=len(rows)
    result['receipt_quality_associations']=[dict(tier=v['tier'],receipt=v['receipt'],
        distinct_automatic_sources=len(v['sources']),
        associated_flagged_posts=len({post for post,_ in v['flagged_posts']}),
        associated_report_statuses=dict(Counter(status for _,status in v['flagged_posts']))) for v in quality.values()]
    result['coverage']={'first_epoch':rows[0][-1] if rows else None,'last_epoch':rows[-1][-1] if rows else None}
    result['limitations']=['Probe and translation events are separate; never sum them as deliveries.',
        'Candidate frequency is deduplicated by source identity, direction and unit, not retry count.',
        'Character opportunity is not a time/token saving estimate. Source versions/grouping can change.',
        'Flags are exact-version associations, not proof a particular memory caused an error. Absence of flags is not an accuracy score.',
        'No automatic promotion. Use source-first evidence and counterexamples; live delivery metrics remain authoritative.']
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--include-text',action='store_true',help='Map exact saved source versions privately; requires --output under data/.')
    args=parser.parse_args()
    output=args.output.resolve() if args.output else None
    if output and not output.is_relative_to(ROOT/'data'):parser.error('Audit output belongs under ignored data/.')
    if args.include_text and output is None:parser.error('Private text requires --output under ignored data/.')
    settings=Settings.load()
    data=report(settings.database_path.with_suffix('.cache.sqlite3'),settings.database_path,include_text=args.include_text)
    if output:
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in data.items() if k!='candidates'},indent=2))
    else:print(json.dumps(data,indent=2))


if __name__=='__main__':main()
