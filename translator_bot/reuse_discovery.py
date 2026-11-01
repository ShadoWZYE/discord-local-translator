"""Bounded private phrase/construction nominations, never inferred approval."""
from collections import defaultdict
import re

from .reuse_planner import candidate_units, template_pattern


def discover(rows, flagged=(), limit=100):
    """No guessed bilingual ngram alignment or cross-field word sequences."""
    flagged=set(flagged);groups={};templates={}
    for row in rows:
        source,target=row['source_language'],row['target_language']
        if ',' in target or source==target:continue
        ident=row['source_id'];text=row['source_text']
        for kind,unit in candidate_units(text):
            if kind not in ('ngram','field','template_candidate') or len(unit)>240:continue
            g=groups.setdefault((source,target,kind,unit),dict(sources=set(),flagged=set(),examples=[]))
            g['sources'].add(ident)
            if ident in flagged:g['flagged'].add(ident)
            if len(g['examples'])<3:g['examples'].append(dict(source_id=ident,source_text=text))
        # Only a complete one-line pair may nominate a bilingual construction.
        # Slot identity must occur once on each side; all fixed text stays exact.
        if '\n' in text or '\n' in row['translated_text'] or len(text)>240 or re.search(r'https?://|`|<[^>]+>',text):continue
        codes=re.findall(r'(?<![\w-])(?=[A-Z0-9_-]*[0-9])[A-Z][A-Z0-9_-]{1,31}(?![\w-])',text)
        if len(codes)!=1:continue
        code=codes[0];translated=row['translated_text']
        boundary=r'(?<![\w-])'+re.escape(code)+r'(?![\w-])'
        if len(re.findall(boundary,translated))!=1:continue
        left=re.sub(boundary,'{code}',text);right=re.sub(boundary,'{code}',translated)
        slots={'code':{'type':'identifier'}}
        if not template_pattern(left,slots).fullmatch(text):continue
        g=templates.setdefault((source,target,left,right),dict(sources=set(),codes=set(),examples=[],flagged=set()))
        g['sources'].add(ident);g['codes'].add(code)
        if ident in flagged:g['flagged'].add(ident)
        if len(g['examples'])<3:g['examples'].append(dict(source_id=ident,source_text=text,candidate_text=translated))
    phrases=[]
    for (source,target,kind,unit),g in groups.items():
        if len(g['sources'])<2:continue
        phrases.append(dict(source=source,target=target,kind=kind,source_text=unit,
            distinct_sources=len(g['sources']),flagged_sources=len(g['flagged']),examples=g['examples'],
            opportunity_characters=(len(g['sources'])-1)*len(unit),
            eligible_for_review=False,decision='source_only_requires_alignment_and_scope_review'))
    constructions=[]
    for (source,target,left,right),g in templates.items():
        if len(g['sources'])<2:continue
        # Disagreeing fixed targets suppress unattended nomination of the family.
        alternatives=sum(k[:3]==(source,target,left) for k in templates)
        example=g['examples'][0]
        constructions.append(dict(source=source,target=target,kind='template',
            source_text=example['source_text'],candidate_text=example['candidate_text'],
            source_template=left,target_template=right,slots={'code':{'type':'identifier'}},
            distinct_sources=len(g['sources']),distinct_slot_values=len(g['codes']),
            flagged_sources=len(g['flagged']),examples=g['examples'],
            eligible_for_review=len(g['sources'])>=3 and len(g['codes'])>=2 and alternatives==1 and not g['flagged'],
            decision='unreviewed_candidate'))
    return dict(phrases=sorted(phrases,key=lambda g:-g['opportunity_characters'])[:limit],
        constructions=sorted(constructions,key=lambda g:(not g['eligible_for_review'],-g['distinct_sources']))[:limit],
        note='Source-only phrases have no inferred target alignment; character opportunity is not measured time saved.')
