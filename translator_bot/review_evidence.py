"""Review receipts bind a linguistic judgment to exact text, not model fluency.

Validation checks evidence completeness and identity. It cannot certify meaning.
Private receipts belong in the local database / ignored data directory.
"""
import hashlib
import json


CHECKS=('actors','actions','ownership','tense_negation_modality','tone_intensity',
        'lexical_specificity','formatting')


def text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def validate_evidence(source,candidate,record):
    return _validate_alignment(source,candidate,record,decisions=('confirmed_error',))


def validate_no_change(source,candidate,record):
    """Evidence completeness only; never eligible for correction approval."""
    return _validate_alignment(source,candidate,record,decisions=('acceptable_variant','no_error'))


def _validate_alignment(source,candidate,record,*,decisions,require_source_ids=True):
    if not isinstance(record,dict) or record.get('decision') not in decisions:
        raise ValueError('Review decision does not match this operation.')
    if record.get('source_sha256')!=text_hash(source) or record.get('candidate_sha256')!=text_hash(candidate):
        raise ValueError('Review evidence does not match the exact source/candidate.')
    if record.get('candidate_text')!=candidate or not candidate.strip() or len(candidate)>1900:
        raise ValueError('Missing candidate or unsupported multi-chunk correction.')
    clauses=record.get('clauses')
    if not isinstance(clauses,list) or not clauses:
        raise ValueError('Record source-to-target clause alignment before approving.')
    for clause in clauses:
        if (not isinstance(clause,dict) or not clause.get('source_span')
                or clause['source_span'] not in source or not clause.get('candidate_span')
                or clause['candidate_span'] not in candidate
                or len(clause.get('finding','').strip())<12
                or len(clause.get('change_reason','').strip())<12):
            raise ValueError('Each clause needs exact spans, a finding and a justified change.')
    checks=record.get('checks',{})
    if not isinstance(checks,dict) or any(not isinstance(checks.get(key),str)
            or len(checks[key].strip())<12 for key in CHECKS):
        raise ValueError('Explain each linguistic/formatting check; a bare pass is insufficient.')
    if not isinstance(record.get('uncertainty'),str) or not record['uncertainty'].strip():
        raise ValueError('Record remaining uncertainty explicitly, including none when appropriate.')
    reference=record.get('reference_check',{})
    if (not isinstance(reference,dict) or not reference.get('kind')
            or not isinstance(reference.get('independent'),bool)
            or len(reference.get('finding','').strip())<12):
        raise ValueError('Describe the cross-check and whether it is genuinely independent.')
    for name in ('source_message_ids','context'):
        if not isinstance(record.get(name),list):raise ValueError('Source IDs and context evidence are required.')
    if (require_source_ids and not record['source_message_ids']) or len(record['context'])>16:
        raise ValueError('Missing source identities or excessive context evidence.')
    for context in record['context']:
        if (not isinstance(context,dict) or not str(context.get('message_id','')).isdigit()
                or not isinstance(context.get('sha256'),str) or len(context['sha256'])!=64):
            raise ValueError('Each context original needs its message ID and exact content hash.')
    return record


def save_approval(connection,feedback_id,source,candidate,record):
    validate_evidence(source,candidate,record)
    connection.execute('CREATE TABLE IF NOT EXISTS feedback_review_evidence ('
        'id INTEGER PRIMARY KEY AUTOINCREMENT, feedback_id INTEGER NOT NULL,'
        'source_hash TEXT NOT NULL, candidate_hash TEXT NOT NULL, evidence_json TEXT NOT NULL,'
        'created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
    connection.execute('INSERT INTO feedback_review_evidence(feedback_id,source_hash,candidate_hash,evidence_json) '
        'VALUES(?,?,?,?)',(feedback_id,text_hash(source),text_hash(candidate),json.dumps(record,ensure_ascii=False)))


def load_approval(connection,feedback_id,source,candidate):
    exists=connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='feedback_review_evidence'").fetchone()
    row=connection.execute('SELECT evidence_json FROM feedback_review_evidence WHERE '
        'feedback_id=? AND source_hash=? AND candidate_hash=? ORDER BY id DESC LIMIT 1',
        (feedback_id,text_hash(source),text_hash(candidate))).fetchone() if exists else None
    if not row:raise ValueError('No matching linguistic approval receipt; run approve before apply.')
    return validate_evidence(source,candidate,json.loads(row[0]))
