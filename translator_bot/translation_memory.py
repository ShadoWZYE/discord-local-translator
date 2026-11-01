"""Explicit linguistic approval for reusable memory, separate from corrections."""
import re
import json
from .review_evidence import _validate_alignment, text_hash


def validate_memory_review(source,target,source_language,target_language,record):
    lexical_review=record.get('reference_check',{}).get('kind')=='source_first_lexical_review'
    _validate_alignment(source,target,record,decisions=('translation_memory',),require_source_ids=not lexical_review)
    if lexical_review and (record['source_message_ids'] or record['context']):
        raise ValueError('A standalone lexical review must not fabricate Discord/context identities.')
    if (record.get('source_language') != source_language or
            record.get('target_language') != target_language):
        raise ValueError('Memory approval is direction-specific.')
    if record.get('scope_kind') not in ('portable','dependency_block'):
        raise ValueError('Approve portable or exact dependency-block scope explicitly.')
    if record.get('decoration') not in ('exact','literal_envelope'):
        raise ValueError('Specify exact-only or literal-envelope reuse.')
    if len(record.get('scope_reason','').strip()) < 40:
        raise ValueError('Explain lexical, referent, gender and register independence/scope.')
    counterexamples=record.get('counterexamples')
    if (not isinstance(counterexamples,list) or len(counterexamples)<2 or
            any(not isinstance(x,str) or len(x.strip())<12 for x in counterexamples)):
        raise ValueError('Document at least two meaningful reuse counterexamples.')
    if record['scope_kind']=='portable':
        if record.get('context_independent') is not True:
            raise ValueError('Portable reuse requires a separate independence judgment.')
        # A review cannot silently set a global English-you register or supply
        # gender for an underspecified English past-tense first person.
        if source_language=='en' and target_language=='ru' and re.search(
                r"\byou(?:r|rs)?\b|\bI\s+(?:was|felt|had|did|\w+ed)\b",source,re.I):
            raise ValueError('English register/gender dependencies require exact context.')
        if record.get('context'):
            raise ValueError('Portable lexical meaning cannot require particular human context.')
    else:
        if not isinstance(record.get('parent_text'),str) or not record['parent_text']:
            raise ValueError('Block approval needs its exact reviewed parent passage.')
        if not isinstance(record.get('conversation_context'),str):
            raise ValueError('Block approval needs exact external context, even if empty.')
        raw=record['conversation_context']
        try:data=json.loads(raw) if raw else {}
        except (ValueError,TypeError):raise ValueError('Block context must use the bot human-context JSON contract.') from None
        if not isinstance(data,dict) or data.get('request') or data.get('previous_translation'):
            raise ValueError('Regeneration output cannot define an approved reusable block.')
        observed={item['sha256'] for item in record['context']}
        if any(not isinstance(item,dict) or not isinstance(item.get('text'),str)
               or text_hash(item['text']) not in observed for item in data.get('originals',[])):
            raise ValueError('Every context text must bind to a reviewed live human evidence hash.')
    revision=record.get('invalidation_revision')
    if (not isinstance(revision,list) or len(revision)!=2 or
            any(type(x) is not int or x<0 for x in revision)):
        raise ValueError('Bind approval to the pre-review invalidation revision.')
    return record
