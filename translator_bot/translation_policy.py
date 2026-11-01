"""Conservative context projection and explicit-actor contracts, not fuzzy reuse."""
import json
import re
from .name_rendering import inflected_aliases

# Deferring an activity to a time is not leaving a newly introduced physical item.
SCHEDULED_ACTIVITY_PATTERN = (
    r'\b(?:остав|отлож)\w*\b'
    r'(?=[^.!?…\n]{0,120}\bна\s+(?:вечер|утро|завтра|потом|выходные)\b)'
    r'(?=[^.!?…\n]{0,120}\b(?:улучшени[ея]|ремонт|уборку|установку|обновление)\b)'
)

COMMON = frozenset(('hello','hi','hey','thanks','thank you','good morning','good night',
    'good evening','goodbye','bye','yes','no','ok','okay','да','нет','привет','здравствуйте','спасибо','большое спасибо',
    'доброе утро','добрый вечер','спокойной ночи','до свидания','пока',
    'done','all done','one moment','just a moment','готово','всё готово','все готово','одну минутку'))
# Direction-specific explicit wellbeing inquiry: English does not need the
# Russian ты/вы distinction. Do NOT apply the reverse EN->RU projection.
WELLBEING_QUESTION = r'как (?:ты себя чувствуешь|вы себя чувствуете)'
RUSSIAN_PERSON_REFERENTS = r'он|она|они|оно|его|е[её]|их|им|ими|ему|ей|н[её]м|нему|ним|ними|ней|не[её]|них|него'
DEPENDENT = re.compile(r'\b(?:he|she|they|him|her|his|their|it|this|that|these|those|there|same|again|too|'
    + RUSSIAN_PERSON_REFERENTS + '|'
    r'это|этот|эта|эти|этим|этими|этому|этой|этого|этих|тот|та|те|тому|тем|теми|той|того|тех|'
    r'там|тут|так|снова|опять|тоже|сам\w*)\b',re.I)

# Cache rule revisions are deliberately explicit and source-scoped. A normal
# code patch/restart MUST NOT invalidate unrelated translations. When changing
# semantics, bump only the affected rule, or invalidate exact reported sources.
CACHE_RULES = (
    ('surprise-exclamation-v1', r'\bholy fucking shit(?=[.!?…]|$)'),
    ('discovery-tag-v1', r'\bthe more you know[, \t]+(?:eh|huh|right)(?=[.!?…]|$)'),
    ('phrase-boundary-v1', r'(?im)^[ \t]*(?:no|yes|okay|ok|done|hello|hi|thanks|thank you|да|нет|спасибо|готово)[ \t]*\n'),
    ('name-case-v1', r'\b(?:'+'|'.join(map(re.escape,inflected_aliases()))+r')\b'),
    ('idiom-asap-v1', r'\bкак\s+только\s*[,—-]?\s*так\s+сразу\b'),
    ('portable-wellbeing-v1', WELLBEING_QUESTION),
    ('scheduled-activity-v1', SCHEDULED_ACTIVITY_PATTERN),
    ('actors-v1', r"\b(?:I|you|he|she|я|ты|вы|он|она)\b"),
    ('layout-v1', r'[\n~\[\]_*]|\|\|'),
    ('swearing-v2', r'\b(?:fuck\w*|damn\w*|shit\w*|пизд\w*|ху[йеёя]\w*|[её]б\w*)\b'),
    ('focus-v1', r'\b(?:not exactly|either)\b'),
    ('discourse-v1', r'\b(?:откуда\s+ты\s+так\w*|никто\s+не\s+отменял|вс[её][.…!]+|раста[яи]\w*)'),
    ('caregiver-v1', r'\bдет\w*\b'),
    ('petname-v1', r'\bbaby (?:girl|boy)\b'),
    ('register-v1', r'\b(?:you|your|come|go|take|my love)\b'),
    ('idiom-leaving-v1', r'\bfuck\s+off\b'),
    ('number-v1', r'\bнаушник\b'),
    ('family-ownership-v1', r'\b(?:отец|папа|мать|мама)\b[\s\S]*\bбрат\w*\b|\bбрат\w*\b[\s\S]*\b(?:отец|папа|мать|мама)\b'),
    ('vocative-talk-v2', r'\bсолнце\b[\s\S]*\b(?:выскажись|аыскажись|выговорись|расскажи|скажи|поговори)\b'),
    ('beneficial-purpose-v2', r'\b(?:на|во)\s+благо\b'),
    ('mood-role-v1',r'\bin (?:a|the|any|no) (?:\w+\s+){0,2}mood\b'),
    ('activity-actor-v1',r"\bI(?:['’]m| am)\b[^.!?\n]*\bwith you\b[^.!?\n]*\b\w+ing\b"),
    ('duration-v1',r'\bнуж(?:ен|на|ны)\b[^.!?\n]*\bна минут[уе]\s*(?:[.!?…]|$)'),
    ('game-rally-v1',r'\bотозвал\w*\b[^.!?\n]*\bсбор\b'),
    ('level-bases-v1',r'\bбаз\w*\s+\d+\s+уровн\w*\b'),
    ('name-adda-v1',r'\b(?:Adda|Адда)\b'),
)


def cache_rules(text):
    return (['standalone-v1'] if standalone_parts(text,'en','ru') else []) + (
        ['context-bound-v1'] if not portable_unit(text,'en','ru') and
        (independent_unit(text,'en','ru') or independent_unit(text,'ru','en')) else []) + [revision for revision,pattern in CACHE_RULES if re.search(pattern,text,re.I)
            and (not revision.startswith('actors-') or _actors(text,'en') or _actors(text,'ru'))]


def layout_units(text, *, sentences=False):
    """Keep separators verbatim; long prose may reuse complete sentences too.

    Sentence boundaries are deliberately conservative. Initials, decimal points,
    and common abbreviations stay together rather than guessing splits.
    """
    pattern = r'\n'
    if sentences or len(text) >= 240:
        pattern += r'|(?<=[.!?…])[ \t]+(?=[A-ZА-ЯЁ])'
    units, separators, start = [], [], 0
    for match in re.finditer(pattern,text):
        if match.group() != '\n' and re.search(
                r'\b(?:mr|mrs|ms|dr|prof|sr|jr|st|vs|etc|approx|т\.д|т\.п|напр)\.$',
                text[:match.start()],re.I):
            continue
        if match.group() != '\n' and re.search(r'\b[A-ZА-ЯЁ]\.$',text[:match.start()]):
            continue
        units.append(text[start:match.start()])
        separators.append(match.group())
        start=match.end()
    units.append(text[start:])
    return units,separators


def lexical_envelope(text):
    """One lexical phrase inside whole-span wrappers; never split inline markup.

    Returns literal prefix/core/suffix. Single tildes, punctuation and emojis
    are author data. URLs, code, mentions, partial wrappers and multiple lines
    fail closed. This parser classifies structure, NOT portable meaning.
    """
    if '\n' in text or '\r' in text:
        return None
    leading = text[:len(text)-len(text.lstrip(' \t'))]
    body, suffix = text[len(leading):], ''
    decoration = r'[ \t.!?…,~\U0001F000-\U0001FAFF\u2600-\u27BF\ufe0e\ufe0f\u200d]*'
    for _ in range(4):
        marker = next((m for m in ('**','__','||','~~','*','_') if body.startswith(m)), None)
        if marker is None:
            break
        end = body.rfind(marker)
        if end < len(marker) or not re.fullmatch(decoration, body[end+len(marker):]):
            return None
        leading += marker
        suffix = marker + body[end+len(marker):] + suffix
        body = body[len(marker):end]
    match = re.fullmatch(r"([^\W\d_]+(?:['’\-][^\W\d_]+)*(?:[ \t]+[^\W\d_]+(?:['’\-][^\W\d_]+)*)*)("+decoration+r')', body)
    return (leading, match[1], match[2]+suffix) if match else None


def standalone_parts(text, source, target):
    """A closed vocabulary only: split exact lexical text from author decoration.

    Never normalize arbitrary clauses, names, markup, URLs or pronouns. Emoji,
    punctuation and tildes remain literal renderer data, not inference output.
    Case and internal word spacing remain part of lexical cache identity.
    """
    if {source,target} != {'en','ru'}:
        return None
    parts = lexical_envelope(text)
    if parts and (parts[1].casefold() in COMMON or
                  (source,target)==('ru','en') and re.fullmatch(WELLBEING_QUESTION,parts[1],re.I)):
        return parts
    return None


def has_portable_sentence(text, source, target):
    """Expose closed-vocabulary phrases in short prose, not arbitrary clauses.

    This selects field boundaries only. Existing cache validation, scope and
    approval rules still decide reuse; all dynamic fields keep joint context.
    """
    units,_=layout_units(text,sentences=True)
    last=next((i for i in range(len(units)-1,-1,-1) if units[i].strip()),-1)
    return len(units)>1 and any(
        (parts:=standalone_parts(unit,source,target)) is not None
        and (i==last or re.search(r'[.!?…]',parts[2]))
        for i,unit in enumerate(units))


def memory_dependency_block(unit, parent, source, target):
    """Candidate smaller scope, usable ONLY after explicit scope review.

    Grammar alone does not authorize removal of distant lexical context. Return
    None on repeated occurrences or uncertain boundaries; callers keep parent.
    External conversation context must still match exactly for block entries.
    """
    if parent is None or parent == unit:
        return unit
    lines = parent.split('\n')
    positions = [i for i,line in enumerate(lines) if line.strip() == unit.strip()]
    if len(positions) != 1:
        return None
    i = positions[0]
    # Preserve the whole paragraph's lexical topic even when grammar alone
    # marks a clause independent (a bat may be an animal or sporting gear).
    left,right = i,i+1
    while left>0 and lines[left-1].strip():left-=1
    while right<len(lines) and lines[right].strip():right+=1
    left,right = dependency_span(lines,left,right,source,target)
    return '\n'.join(lines[left:right])


def portable_unit(text, source, target):
    """Closed phrase vocabulary only; grammar is not proof of portable meaning."""
    if {source,target}!={'en','ru'}:return False
    if standalone_parts(text,source,target) is not None:return True
    plain=re.sub(r'[\U0001F000-\U0001FAFF\u2600-\u27BF\ufe0e\ufe0f\u200d]','',text).strip()
    words=re.sub(r'[.!?…]+$','',plain).strip().casefold()
    return words in COMMON or bool(re.fullmatch(r'h+e+l{2,}o+|h+i+|t+h+a+n+k+s+|п+р+и+в+е+т+|с+п+а+с+и+б+о+',words))


def independent_unit(text, source, target):
    """Grammatical fragment eligibility, not proof of context-portable meaning."""
    plain=text.strip()
    if standalone_parts(plain,source,target) is not None:
        return True
    greeting=re.sub(r'[\U0001F000-\U0001FAFF\u2600-\u27BF\ufe0e\ufe0f\u200d]','',plain).strip()
    words=re.sub(r'[.!?…]+$','',greeting).strip().casefold()
    if words in COMMON or re.fullmatch(r'h+e+l{2,}o+|h+i+|t+h+a+n+k+s+|п+р+и+в+е+т+|с+п+а+с+и+б+о+',words):
        return True
    if unfinished_clause(plain,source) or DEPENDENT.search(plain):
        return False
    if len(re.findall(r'\w+',plain))<3:
        return False
    if source=='ru' and target=='en':
        return bool(re.match(r'^(?:я|ты|вы|мы)\b',plain,re.I))
    # English past/adjectival/conditional clauses may need gender/context in RU.
    # Even future 'you' needs ты/вы (including object тебе/вам), so don't
    # generalize it across conversations with different register/participants.
    return source=='en' and target=='ru' and not re.search(r'\byou(?:r|rs)?\b',plain,re.I) and bool(re.match(
        r"^(?:(?:I|we)\s+(?:will|can|cannot|can't|do not|don't)\s+\w+|"
        r"(?:the\s+[a-z]+|[a-z]+(?:\s+[a-z]+)?)\s+(?:is|are|has|have)\s+\w+)",plain,re.I))


def unfinished_clause(text,source):
    """No final period is not itself incompleteness in chat.

    This classifies syntax only. Fragment identity still keeps external/parent
    context; none of these checks establishes portable word sense.
    """
    plain=text.strip()
    if not plain or re.search(r'[,;:\-—]$|\.{3}$|…$',plain):return True
    if any(plain.count(a)!=plain.count(b) for a,b in (('(',')'),('[',']'),('{','}'))):return True
    core=re.sub(r'[.!?]+$','',plain).rstrip()
    tail=(r"\b(?:and|or|but|because|if|of|to|with|for|from|the|a|an|my|your|not|"
          r"will|would|can|could|should|must|is|are|was|were|has|have|do|does|did)" if source=='en' else
          r'\b(?:и|или|но|если|чтобы|потому|для|из|с|со|к|ко|на|в|во|не|ни|что|хочу|хочешь|хотим|могу|можешь|можем|буду|будешь|будем)')
    if re.search(tail+r'$',core,re.I):return True
    if re.search(r'^[\s]*(?:and|but|because|if|so|и|но|а|если|потому|чтобы)\b',core,re.I):return True
    return False


def dependency_reasons(text, source, target):
    """Cheap conservative classification, never a guessed coreference answer."""
    if portable_unit(text,source,target):
        return ()
    if independent_unit(text,source,target):return ('lexical_context',)
    reasons=[]
    if DEPENDENT.search(text): reasons.append('referent')
    if unfinished_clause(text,source): reasons.append('unfinished')
    if re.search(r'\byou(?:r|rs)?\b',text,re.I) and target=='ru': reasons.append('register')
    if source=='en' and target=='ru' and re.search(r"\b(?:I|you)\s+(?:was|were|felt|had|did|\w+ed)\b",text,re.I):
        reasons.append('gender')
    if re.search(r'(?:^\s*(?:and|but|because|if|so|и|но|а|если|потому)\b|\b(?:of|to|with|and|because|если|чтобы|потому что|для|из|с)\s*$)',text,re.I):
        reasons.append('continuation')
    return tuple(reasons or ['unproven'])


def dependency_span(lines,start,end,source,target):
    """Expand an edited block over connected lines without inventing antecedents.

    An unresolved pronoun retains its whole preceding paragraph. An incomplete
    clause grows across line boundaries; dependent following lines grow with it.
    Blank lines are layout, not proof that a referent is resolved. Unresolved
    external references remain in conversation context, never globally cached.
    """
    if start==end:
        return start,end
    # Share inflected personal referents, without treating every demonstrative
    # or discourse particle as a dependency on the entire previous paragraph.
    referent=re.compile(r'\b(?:he|she|him|her|his|their|they|it|'+RUSSIAN_PERSON_REFERENTS+r')\b',re.I)
    continuation=re.compile(r'^\s*(?:and|but|because|if|so|и|но|а|если|потому|чтобы)\b',re.I)
    terminal=lambda line:bool(re.search(r'[.!?…][\s\]\)\u201d\u00bb]*$',line))
    left,right=start,end
    if any(referent.search(line) for line in lines[start:end]):
        while left>0 and lines[left-1].strip(): left-=1
    while left>0 and lines[left-1].strip() and (not terminal(lines[left-1]) or continuation.search(lines[left])):
        left-=1
    while right<len(lines) and lines[right].strip() and (
            not terminal(lines[right-1]) or continuation.search(lines[right]) or referent.search(lines[right])):
        right+=1
    return left,right


def effective_context(text, raw, source, target):
    if not raw:
        return ''
    try:
        data=json.loads(raw)
    except (ValueError,TypeError):
        return raw  # Unknown callers retain exact semantics/cache isolation.
    if not isinstance(data,dict):
        return raw
    if data.get('previous_translation') or data.get('request'):
        return json.dumps(data,ensure_ascii=False,sort_keys=True)  # Explicit regeneration.
    if portable_unit(text,source,target):
        return ''
    # IDs distinguish roles, not lexical meaning. Canonical role labels retain
    # human names/text/order/reply targets while avoiding incidental ID churn.
    current=data.get('current_speaker',{})
    current_id=current.get('id') if isinstance(current,dict) else None
    ids={current_id:'current'} if current_id is not None else {}
    def speaker(value):
        if not isinstance(value,dict):return value
        value=dict(value)
        if 'id' in value:
            key=value['id']
            if key not in ids:ids[key]=f'other_{len(ids)}'
            value['id']=ids[key]
        return value
    data=dict(data)
    data['current_speaker']=speaker(current)
    data['originals']=[dict(item,speaker=speaker(item.get('speaker',{})))
        for item in data.get('originals',[]) if isinstance(item,dict)]
    return json.dumps(data,ensure_ascii=False,sort_keys=True)


def _actors(text,language):
    if language=='en':
        text=re.sub(r"\bI\s+(?:guess|think|suppose|mean|believe)\b",'',text,flags=re.I)
        first=bool(re.search(r"\bI(?:['’](?:m|ve|ll|d)|\s+\w+)",text,re.I))
        second=bool(re.search(r"\byou(?:['’](?:re|ve|ll|d)|\s+(?:are|were|have|had|will|would|can|could|do|did|know|want|think|make|like|love|need))\b",text,re.I))
        # Explicit clause-initial You remains the subject with an unseen verb;
        # a closed auxiliary list must not misclassify "You notice ...; I ..."
        # as replacing you with I. Object pronouns inside clauses stay excluded.
        second=second or bool(re.search(
            r'(?:^|[.!?;:,]\s*)you\s+(?!(?:and|or|to|with|of|for)\b)[A-Za-z]+',text,re.I))
        # Negative auxiliary contractions are still explicit second-person
        # subjects, not an absent actor. In mixed implicit-RU/explicit-EN prose
        # missing them falsely labels a legitimate I ... you wouldn't as I-only.
        second = second or bool(re.search(
            r"\byou\s+(?:(?:are|were|have|has|had|do|does|did|would|could|should|must|ca|wo|is|was)n['’]t|"
            r"should|must|hear|heard|see|saw|feel|felt)\b",text,re.I))
        second = second or bool(re.search(
            r'(?:^|[.!?\n]\s*)(?:(?:how|what|why|when|where)\b[^.!?\n]{0,30}?\s+)?'
            r'(?:are|were|do|did|will|would|can|could|have|should)\s+you\b|^\s*(?:and\s+)?you\s*[?]?$'
            ,text,re.I))
    else:
        text=re.sub(r'\bя\s+(?:думаю|полагаю|считаю|предполагаю)\b','',text,flags=re.I)
        first=bool(re.search(r'\b(?:я|делаю|знаю|хочу|могу|пытаюсь|надеюсь)\b|\bу\s+(?:меня|нас)\b',text,re.I))
        second=bool(re.search(r'\b(?:ты|вы|делаешь|делаете|знаешь|знаете|хочешь|хотите|можешь|можете|пытаешься|пытаетесь)\b|\bу\s+(?:тебя|вас)\b',text,re.I))
    male=bool(re.search(r'\bhe\b' if language=='en' else r'\bон\b',text,re.I))
    female=bool(re.search(r'\bshe\b' if language=='en' else r'\bона\b',text,re.I))
    return {person for person,present in ((1,first),(2,second),(3,male),(4,female)) if present}


def actor_drift(source,output,source_code,target):
    """Reject demonstrated person reversal; absence/implicit subjects are allowed."""
    if {source_code,target}!={'en','ru'}:return False
    def reversed_role(a,b):
        expected=_actors(a,source_code); actual=_actors(b,target)
        # Dative experiencers can be expressed with a different grammatical
        # subject: I like you -> Ты мне нравишься. That is not an actor flip.
        if target=='ru' and re.search(r'\b(?:мне|тебе|вам)\b',b,re.I):
            if re.search(r'\b(?:нрав\w*|дорог\w*|нуж\w*|хочется|кажется)\b',b,re.I):
                return False
        if source_code=='ru' and re.search(r'\b(?:мне|тебе|вам)\b',a,re.I):
            if re.search(r'\b(?:нрав\w*|дорог\w*|нуж\w*|хочется|кажется)\b',a,re.I):
                return False
        if len(expected)!=1:
            return False
        person=next(iter(expected))
        opposite={1:2,2:1,3:4,4:3}[person]
        # Demonstrated opposite-person/gender substitution only. A Russian
        # object pronoun can become English it, while у меня can become I have;
        # do not mistake that grammatical restructuring for a human actor flip.
        return opposite in actual and person not in actual
    split=lambda value:[s.strip() for s in re.split(r'[.!?…\n~]+',value) if s.strip()]
    before,after=split(source),split(output)
    if len(before)==len(after):
        return any(reversed_role(a,b) for a,b in zip(before,after))
    # Never compare a whole mixed message's one explicit actor against an
    # English I legitimately supplied for an earlier subjectless Russian clause.
    # A merged output is only demonstrably reversed when EVERY source unit
    # explicitly carries the same single actor.
    actors=[_actors(unit,source_code) for unit in before]
    return bool(actors) and all(len(a)==1 and a==actors[0] for a in actors) and reversed_role(source,output)


def actor_contracts(parts, language):
    if language not in ('en','ru'):
        return {}
    labels={1:'CURRENT SPEAKER (I/я), not the addressee',2:'ADDRESSEE (you/ты/вы), not the speaker',
            3:'Explicit male third person (he/он), not she or you',
            4:'Explicit female third person (she/она), not he or you'}
    return {key:labels[next(iter(actors))] for key,text in parts.items()
            if len(actors:=_actors(text,language))==1}


def has_role_changes(text,language):
    if language not in ('en','ru'):
        return False
    contracts=[_actors(unit,language) for unit in layout_units(text,sentences=True)[0]]
    # Proactively separate first/second-person reversals. Third-person prose
    # often refers to grammatical-gender objects (Discord -> он -> it), and
    # mixed-viewpoint narration should not be restructured just for that.
    return {1,2}.issubset({next(iter(actors)) for actors in contracts if len(actors)==1})


def focus_contracts(parts,source,target):
    if (source,target)!=('en','ru'):
        return {}
    result={}
    for key,text in parts.items():
        operators=[]
        if re.search(r'\bnot exactly\b',text,re.I):
            operators.append('HEDGE: not exactly = не особо / не совсем / не то чтобы; not a categorical denial')
        if (re.search(r"\b(?:not|\w+n['’]t)\b.*\beither\b",text,re.I)
                and not re.search(r'\beither\b.*\bor\b',text,re.I)):
            operators.append('ADDITIVE NEGATIVE: either = тоже / также, explicitly retain it')
        if operators:result[key]=operators
    return result
