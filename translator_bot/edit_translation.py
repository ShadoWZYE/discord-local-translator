"""Conservative line-aligned edits: one contextual block, no sentence guessing."""
from difflib import SequenceMatcher


def edit_window(old,new,translated,*,independent_rows=False,source=None,target=None):
    before=old.split('\n'); after=new.split('\n'); output=translated.split('\n')
    if old==new:return (0,0,0,0)
    # Length alone is not a grammatical safety property; retain all guards.
    if len(new)<80 or len(before)<3 or len(before)!=len(output):return None
    changes=[c for c in SequenceMatcher(a=before,b=after,autojunk=False).get_opcodes() if c[0]!='equal']
    if not changes:return None
    a0,a1=changes[0][1],changes[-1][2]
    b0,b1=changes[0][3],changes[-1][4]
    # Refresh neighboring lines too: continuations/pronouns can depend on the
    # edit. Everything else is reused byte-for-byte, including manual wording.
    left=min(a0,b0,1);right=min(len(before)-a1,len(after)-b1,1)
    a0-=left;b0-=left;a1+=right;b1+=right
    if source and target and not independent_rows:
        from .translation_policy import dependency_span,independent_unit
        changed_before=before[changes[0][1]:changes[-1][2]]
        changed_after=after[changes[0][3]:changes[-1][4]]
        # Fully standalone neighbors need no refresh; unknown grammar keeps
        # the existing neighbor cushion, explicit dependencies expand it.
        if (changed_before and changed_after and all(independent_unit(line,source,target)
                for line in before[max(0,a0):a1]+after[max(0,b0):b1] if line.strip())):
            a0,a1=changes[0][1],changes[-1][2]
            b0,b1=changes[0][3],changes[-1][4]
        a0,a1=dependency_span(before,a0,a1,source,target)
        b0,b1=dependency_span(after,b0,b1,source,target)
    block='\n'.join(after[b0:b1])
    if not block.strip() or len(block)>len(new)*0.55:return None
    if before[:a0]!=after[:b0] or before[a1:]!=after[b1:]:return None
    # An explicit participant/name change can affect distant agreement. Use a
    # full translation rather than claiming local reuse is safe in that case.
    import re
    anchors=lambda s:set(re.findall(r'\b(?:I|you|he|she|they|я|ты|вы|он|она|они|[A-ZА-ЯЁ][a-zа-яё]{2,})\b',s))
    if not independent_rows and anchors('\n'.join(before[a0:a1]))!=anchors(block):return None
    return a0,a1,b0,b1
