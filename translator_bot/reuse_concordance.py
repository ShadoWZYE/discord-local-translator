"""Historical source/output agreement ranks reviews; it never approves scope."""
from collections import defaultdict
from contextlib import closing
from datetime import datetime,timedelta,timezone
import hashlib
import json
import sqlite3

from .translation_policy import lexical_envelope
from .reuse_discovery import discover


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def concordance(rows,flagged=()):
    groups={};flagged=set(flagged)
    for row in rows:
        source=row['source_language'];target=row['target_language']
        if ',' in target or source==target:continue
        left=row['source_text'].split('\n');right=row['translated_text'].split('\n')
        if len(left)!=len(right):continue  # Never guess alignment across lines.
        for original,translated in zip(left,right):
            a=lexical_envelope(original);b=lexical_envelope(translated)
            if not a or not b or not 1<=len(a[1])<=120 or len(a[1].split())>12:continue
            key=(source,target,a[1]);ident=row['source_id']
            group=groups.setdefault(key,dict(source=source,target=target,source_text=a[1],
                votes=defaultdict(set),sources=set(),flagged=set(),examples=[]))
            group['sources'].add(ident);group['votes'][b[1]].add(ident)
            if ident in flagged:group['flagged'].add(ident)
            if len(group['examples'])<8:
                group['examples'].append(dict(source_id=ident,source_text=original,candidate_text=translated,
                    source_hash=digest(row['source_text']),parent_hash=digest(row['source_text']),
                    alignment='whole' if len(left)==1 else 'equal_line_count_only'))
    result=[]
    for key,g in groups.items():
        if len(g['sources'])<2:continue
        variants=sorted(g['votes'].items(),key=lambda v:(-len(v[1]),v[0]))
        chosen,ids=variants[0]
        ambiguous={ident for ident in g['sources'] if sum(ident in votes for votes in g['votes'].values())>1}
        supporting=ids-ambiguous
        count=len(g['sources']);percent=100*len(supporting)/count
        candidate=dict(source=g['source'],target=g['target'],source_text=g['source_text'],candidate_text=chosen,
            distinct_sources=count,dominant_support=len(supporting),agreement_percent=round(percent,2),
            output_variants=[dict(text=text,distinct_sources=len(votes)) for text,votes in variants],
            ambiguous_alignment_sources=len(ambiguous),flagged_sources=len(g['flagged']),
            source_ids=sorted(g['sources']),examples=g['examples'],decision='unreviewed_candidate',
            eligible_for_review=len(supporting)>=3 and percent>=95 and not ambiguous and not g['flagged'])
        candidate['fingerprint']=digest(candidate)
        candidate['scope_fingerprint']=digest([candidate[k] for k in ('source','target','source_text','candidate_text')])
        result.append(candidate)
    return sorted(result,key=lambda v:(not v['eligible_for_review'],-v['dominant_support']*len(v['source_text']),v['fingerprint']))


def historical_report(path):
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'translation_snapshots' not in tables:return dict(sample_size=0,candidates=[])
        rows=[dict(r) for r in db.execute('SELECT source_id,source_text,translated_text,source_language,target_language FROM translation_snapshots ORDER BY source_id DESC LIMIT 5000')]
        flagged=[r[0] for r in db.execute('SELECT DISTINCT source_message_id FROM translation_feedback')] if 'translation_feedback' in tables else []
    patterns=discover(rows,flagged)
    for candidate in patterns['constructions']:
        candidate['scope_fingerprint']=digest([candidate[k] for k in ('source','target','source_template','target_template')])
        candidate['fingerprint']=digest(candidate)
    return dict(schema='historical-concordance-v2',sample_size=len(rows),candidates=concordance(rows,flagged),patterns=patterns,
        limitations=['At most the latest 5000 saved snapshots, not independent model experiments or live hit rates.',
            'Duplicate source IDs count once. Same-model agreement can repeat a systematic error.',
            'Literal envelopes remove decoration for analysis only; equal line counts do not certify alignment.',
            '95 percent agreement and three supporting source IDs only nominate a review, never approve portability.',
            'Any reported source is excluded from unattended nomination; no flag is not proof of correctness.'])


def review_candidates(report,attempts,approved_sources=(),*,now=None):
    """At most five new scopes per rolling day; unchanged attempts stay retired."""
    now=now or datetime.now(timezone.utc);cutoff=now-timedelta(days=1)
    recent=0
    for receipt in attempts.values():
        try:stamp=datetime.fromisoformat(receipt['attempted_at'])
        except (ValueError,TypeError,KeyError):continue
        if stamp.tzinfo is not None and stamp>=cutoff:recent+=1
    selected=[];approved=set(approved_sources)
    retired_scopes={v.get('scope_fingerprint') for v in attempts.values() if v.get('outcome') in ('rejected','deferred')}
    for candidate in report['candidates']+report.get('patterns',{}).get('constructions',[]):
        if (not candidate['eligible_for_review'] or candidate['fingerprint'] in attempts or
            candidate.get('scope_fingerprint') in retired_scopes or
            (candidate['source'],candidate['target'],candidate.get('source_template',candidate['source_text'])) in approved):continue
        selected.append(candidate)
        if len(selected)>=max(0,5-recent):break
    return selected if recent<5 else []
