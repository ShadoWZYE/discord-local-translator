"""Privately rank source/output agreement and record bounded review attempts."""
import argparse
from contextlib import closing
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import sys
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from translator_bot.config import Settings
from translator_bot.reuse_concordance import historical_report


def active_receipt(cache_path,tier,receipt,candidate):
    if tier not in ('memory','rule') or type(receipt) is not int:return False
    table,approvals=('translation_memory','memory_approvals') if tier=='memory' else ('reuse_rules','reuse_approvals')
    with closing(sqlite3.connect(cache_path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        row=db.execute(f'SELECT a.evidence_json FROM {table} m JOIN {approvals} a ON a.id=m.approval_id '
            'JOIN cache_revision c ON c.id=1 LEFT JOIN source_revisions s ON s.source=m.source '
            'WHERE m.approval_id=? AND m.global_revision=c.revision AND m.source_revision=COALESCE(s.revision,0)',(receipt,)).fetchone()
    if not row:return False
    record=json.loads(row[0])
    return all(record.get(k)==candidate[v] for k,v in [('source_text','source_text'),('candidate_text','candidate_text'),
        ('source_language','source'),('target_language','target')])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path)
    p.add_argument('--record',type=Path,help='Captured private candidate file from the reviewer run.')
    p.add_argument('--fingerprint')
    p.add_argument('--outcome',choices=('approved','rejected','deferred'))
    p.add_argument('--reason')
    p.add_argument('--receipt',type=int)
    p.add_argument('--tier',choices=('memory','rule'))
    args=p.parse_args();settings=Settings.load()
    output=args.output.resolve() if args.output else None
    if output and not output.is_relative_to(ROOT/'data'):p.error('Private reports belong under ignored data/.')
    if args.record:
        path=args.record.resolve()
        if not path.is_relative_to(ROOT/'data'):p.error('Use captured candidates under ignored data/.')
        candidates=json.loads(path.read_text(encoding='utf-8'))
        if isinstance(candidates,dict):candidates=candidates['candidates']+candidates.get('patterns',{}).get('constructions',[])
        candidate=next((v for v in candidates if v['fingerprint']==args.fingerprint),None)
        if not candidate or not args.outcome or not args.reason or len(args.reason.strip())<20:
            p.error('Record the captured fingerprint, outcome and a specific private review explanation.')
        if args.outcome=='approved' and not active_receipt(settings.database_path.with_suffix('.cache.sqlite3'),args.tier,args.receipt,candidate):
            p.error('Approved outcome requires an active exact linguistic approval receipt.')
        ledger=ROOT/'data'/'maintenance'/'memory_review_attempts.json'
        records=json.loads(ledger.read_text(encoding='utf-8')) if ledger.exists() else {}
        records[args.fingerprint]=dict(fingerprint=args.fingerprint,outcome=args.outcome,reason=args.reason,
            scope_fingerprint=candidate.get('scope_fingerprint'),receipt=args.receipt,tier=args.tier,
            attempted_at=datetime.now(timezone.utc).isoformat())
        ledger.parent.mkdir(parents=True,exist_ok=True)
        temporary=ledger.with_suffix('.tmp');temporary.write_text(json.dumps(records,indent=2),encoding='utf-8');temporary.replace(ledger)
        print(json.dumps({'recorded':args.fingerprint,'outcome':args.outcome,'linguistic_approval_created':False}))
        return
    if output is None:p.error('Historical source/output material requires --output under ignored data/.')
    report=historical_report(settings.database_path)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'sample_size':report['sample_size'],'recurring_candidates':len(report['candidates']),
        'eligible_for_review':sum(v['eligible_for_review'] for v in report['candidates']),
        'source_only_phrase_candidates':len(report.get('patterns',{}).get('phrases',[])),
        'construction_candidates':len(report.get('patterns',{}).get('constructions',[]))}))


if __name__=='__main__':main()
