"""Exact, evidence-scoped reuse. No fuzzy matching or guessed morphology."""
from dataclasses import dataclass
import hashlib
import json
import re
from functools import wraps

from .translation_memory import validate_memory_review


def reuse_session(method):
    @wraps(method)
    def run(self,*args,**kwargs):
        previous=getattr(self._context,'reuse_state',None)
        self._context.reuse_state={'dependencies':{},'rules':None}
        audit=getattr(self._context,'reuse_audit',None)
        if audit is not None:
            direction=[args[1] if len(args)>1 else kwargs.get('source','unknown'),
                       args[2] if len(args)>2 else kwargs.get('targets',kwargs.get('target','unknown'))]
            if direction not in audit.setdefault('directions',[]):audit['directions'].append(direction)
        try:
            result=method(self,*args,**kwargs)
            deps=list(self._context.reuse_state['dependencies'].values())
            if deps and (self._disk_cache is None or not self._reuse_store().valid(deps)):
                if method.__name__=='cached_translation':return None
                # Import lazily to avoid a translation/planner import cycle.
                from .translation import TranslationUnavailable
                raise TranslationUnavailable('Reuse approval changed during translation; retry with current evidence.')
            audit=getattr(self._context,'reuse_audit',None)
            if audit is not None:
                audit['returned_translation']=result is not None
                if result is not None:
                    audit['reused_characters']=max(audit.get('reused_characters',0),getattr(result,'reused_characters',0))
            return result
        finally:self._context.reuse_state=previous
    return run


@dataclass(frozen=True)
class ReuseSpan:
    start: int
    end: int
    value: str
    receipt: int
    dependency: tuple
    tier: str


def canonical_rule(record):
    return {key: record[key] for key in (
        'rule_kind', 'usage', 'source_language', 'target_language',
        'source_text', 'candidate_text') + (
        ('source_template', 'target_template', 'slots') if record['rule_kind']=='template' else ())}


def rule_scope(record):
    # Lexical scopes coincide with existing phrase invalidation. Templates
    # have a distinct family scope: flagging any instance revokes the family.
    text=('fragment:'+record['source_text'] if record['rule_kind']=='lexical' else
          'template:'+json.dumps(canonical_rule(record),ensure_ascii=False,sort_keys=True))
    return hashlib.sha256(text.encode('utf-8')).digest()


def template_pattern(template, slots):
    if any(c in re.sub(r'\{[a-z][a-z0-9_]*\}','',template) for c in '{}'):
        raise ValueError('Only named literal slots are allowed; no format conversions or attribute access.')
    parts=re.split(r'(\{[a-z][a-z0-9_]*\})',template)
    names=[];pattern=''
    for part in parts:
        if re.fullmatch(r'\{[a-z][a-z0-9_]*\}',part):
            name=part[1:-1]
            if name not in slots or name in names:raise ValueError('Each template slot must occur exactly once.')
            names.append(name)
            spec=slots[name]
            if spec.get('type')=='identifier':
                # Fixed ASCII codes, never free prose, names or instructions.
                value=r'(?=[A-Z0-9_-]*[0-9])[A-Z][A-Z0-9_-]{1,31}'
            elif spec.get('type')=='enum' and isinstance(spec.get('values'),list) and 1<=len(spec['values'])<=32:
                values=spec['values']
                if any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}',v) for v in values):
                    raise ValueError('Enum slots retain exact bounded literal codes/numbers only.')
                value='(?:'+'|'.join(re.escape(v) for v in sorted(set(values),key=len,reverse=True))+')'
            else:raise ValueError('Only identifier or explicitly enumerated literal slots are supported.')
            pattern+=f'(?P<{name}>{value})'
        else:pattern+=re.escape(part)
    if set(names)!=set(slots):raise ValueError('Every slot must occur in both templates.')
    return re.compile(pattern)


def validate_rule(record):
    source=record.get('source_text','');target=record.get('candidate_text','')
    validate_memory_review(source,target,record.get('source_language'),record.get('target_language'),record)
    if record['scope_kind']!='portable' or record['decoration']!='exact':
        raise ValueError('Reuse rules need separate portable, exact-form evidence.')
    if record.get('rule_kind')=='lexical':
        if record.get('usage') not in ('field','embedded_invariant'):
            raise ValueError('Specify complete-field or embedded-invariant usage.')
        if not re.fullmatch(r"[^\W\d_]+(?:[ '\u2019-][^\W\d_]+)*",source):
            raise ValueError('Lexical rules bind exact words/phrases, not syntax or arbitrary patterns.')
        if record['usage']=='embedded_invariant' and (
                record.get('target_form_invariant') is not True or
                len(record.get('embedding_reason','').strip())<40):
            raise ValueError('Embedding needs explicit word-sense, inflection and boundary justification.')
    elif record.get('rule_kind')=='template':
        if record.get('usage')!='field':raise ValueError('Templates match complete fields only.')
        slots=record.get('slots')
        if not isinstance(slots,dict) or not 1<=len(slots)<=4:raise ValueError('Define 1-4 constrained slots.')
        for key in ('source_template','target_template'):
            if not isinstance(record.get(key),str) or len(record[key])>1900 or '\n' in record[key]:
                raise ValueError('Templates are exact single-line constructions.')
        pattern=template_pattern(record['source_template'],slots)
        template_pattern(record['target_template'],slots)
        match=pattern.fullmatch(source)
        if not match or record['target_template'].format(**match.groupdict())!=target:
            raise ValueError('The reviewed concrete example must instantiate both templates exactly.')
        if len(record.get('slot_reason','').strip())<40:
            raise ValueError('Explain why every allowed slot preserves grammar, meaning and literal identity.')
    else:raise ValueError('Specify lexical or template rule kind.')
    return record


def rule_matches(text, row, *, embedded=False):
    """Return longest candidates; caller resolves overlap before rendering."""
    r=row['record'];dep=row['dependency'];receipt=row['receipt']
    if r['rule_kind']=='template':
        if embedded:return []
        match=template_pattern(r['source_template'],r['slots']).fullmatch(text)
        return [ReuseSpan(0,len(text),r['target_template'].format(**match.groupdict()),receipt,dep,'template')] if match else []
    if embedded and r['usage']!='embedded_invariant':return []
    pattern=re.compile(r"(?<![\w'\u2019-])"+re.escape(r['source_text'])+r"(?![\w'\u2019-])")
    return [ReuseSpan(m.start(),m.end(),r['candidate_text'],receipt,dep,'lexical') for m in pattern.finditer(text)
            if embedded or m.span()==(0,len(text))]


def select_spans(text,rows,*,embedded=False,protected=()):
    candidates=[span for row in rows for span in rule_matches(text,row,embedded=embedded)
                if not any(a<span.end and span.start<b for a,b in protected)]
    # Overlap is resolved left-to-right, longest first at the same position.
    # Conflicting exact approvals fail closed, not an arbitrary winner.
    chosen=[];end=0
    blocked=[(s.start,s.end) for s in candidates if any(
        (c.start,c.end)==(s.start,s.end) and c.value!=s.value for c in candidates)]
    for span in sorted(candidates,key=lambda s:(s.start,-(s.end-s.start),s.receipt)):
        if any(a<span.end and span.start<b for a,b in blocked):continue
        if span.start>=end:chosen.append(span);end=span.end
    return chosen


def candidate_units(text):
    """Bounded source-only pattern discovery. Repetition never grants approval."""
    # Do not learn words inside URLs, Discord tokens or literal code blocks.
    text=re.sub(r'```[\s\S]*?```|`[^`\n]*`|https?://\S+|<[^>]+>','\n',text)
    units={('field',part.strip()) for part in re.split(r'\n|(?<=[.!?…])\s+',text)
           if part.strip() and any(c.isalpha() for c in part)}
    fields=[part for _,part in sorted(units)]
    # Field candidates first; bounded ngrams do not claim grammatical scope.
    result=sorted(units,key=lambda v:(-len(v[1]),v[1]))[:24]
    for _,unit in sorted(units):
        skeleton=re.sub(r'(?<!\w)(?=[A-Za-z0-9_-]*\d)[A-Za-z][A-Za-z0-9_-]*(?!\w)', '{identifier}',unit)
        skeleton=re.sub(r'(?<!\w)\d+(?!\w)','{number}',skeleton)
        if skeleton!=unit:result.append(('template_candidate',skeleton))
        if len(result)>=32:break
    for n in (2,3,4,1):
        seen=set()
        added=0
        for field in fields:
            words=list(re.finditer(r"[^\W\d_]+(?:['\u2019-][^\W\d_]+)*",field))
            for i in range(len(words)-n+1):
                matches=words[i:i+n]
                if any(not re.fullmatch(r'[ \t]+',field[a.end():b.start()]) for a,b in zip(matches,matches[1:])):continue
                unit=field[matches[0].start():matches[-1].end()]
                if unit not in seen:
                    result.append(('word' if n==1 else 'ngram',unit));seen.add(unit);added+=1
                if added>=16 or len(result)>=96:break
            if added>=16 or len(result)>=96:break
    return result
