"""Audited terminal findings, distinct from linguistic correction approvals.

No cache seeding or translation edits occur here. Receipts bind to exact report
and live evidence; only verified missing posts qualify as obsolete.
"""
import json
from urllib.parse import quote

from .maintenance import fingerprint
from .review_evidence import text_hash, validate_no_change


def resolve_report(store, report_id, evidence, request, guild_ids):
    row = store._connection.execute('SELECT * FROM translation_feedback WHERE id=?', (report_id,)).fetchone()
    if not row or row['guild_id'] not in guild_ids:
        raise ValueError('Missing or out-of-scope report.')
    row = dict(row)
    decision = evidence.get('decision')
    status = {'acceptable_variant': 'closed_no_change', 'no_error': 'closed_no_change',
              'obsolete': 'obsolete'}.get(decision)
    if not status or len(evidence.get('reason', '').strip()) < 20:
        raise ValueError('Document a terminal finding and its specific reason.')
    if evidence.get('report_fingerprint') != fingerprint(row):
        raise ValueError('Report changed since review.')
    if row['status'] not in ('open', status):
        raise ValueError('Only open reports or retries of the same resolution can close.')
    channel = row['channel_id']

    def get(message_id):
        return request('GET', f'/channels/{channel}/messages/{message_id}', allow_missing=True)

    post = get(row['translation_message_id'])
    link = store.get_translation_link(row['translation_message_id'])
    sources = store.get_batch_sources(link['source_message_id']) if link else [row['source_message_id']]
    originals = [get(message_id) for message_id in sources]
    if status == 'obsolete':
        if post is not None:
            raise ValueError('A surviving reported translation needs review, not obsolete closure.')
        if evidence.get('missing_translation_id') != row['translation_message_id']:
            raise ValueError('Bind obsolete evidence to the exact missing post.')
    else:
        if (not link or post is None or any(m is None for m in originals)
                or int(post.get('webhook_id', 0)) != link['webhook_id']):
            raise ValueError('No live owned translation and complete source grouping to review.')
        source = '\n'.join(m['content'] for m in originals)
        if source != row['source_text'] or set(evidence.get('source_message_ids', [])) != set(sources):
            raise ValueError('Live source or grouping changed.')
        # Alignment completeness is shared, but the decision is NOT corrective
        # approval and cannot load as an approval through load_approval().
        validate_no_change(source, post['content'], evidence)
        for context in evidence['context']:
            human = get(context['message_id'])
            if (human is None or human.get('webhook_id') or human['author'].get('bot')
                    or text_hash(human['content']) != context['sha256']):
                raise ValueError('Reviewed human context changed.')
    # Re-read immediately before recording a terminal finding. A deletion or
    # edit during the check must not clear a now-stale flag.
    if get(row['translation_message_id']) != post or [get(i) for i in sources] != originals:
        raise ValueError('Live evidence changed during resolution.')
    with store._lock, store._connection:
        current = store._connection.execute('SELECT * FROM translation_feedback WHERE id=?', (report_id,)).fetchone()
        if not current or fingerprint(dict(current)) != evidence['report_fingerprint']:
            raise ValueError('Report changed during resolution.')
        store._connection.execute('CREATE TABLE IF NOT EXISTS feedback_resolutions ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT,feedback_id INTEGER NOT NULL,'
            'decision TEXT NOT NULL,record_json TEXT NOT NULL,evidence_json TEXT NOT NULL,'
            'cleanup_verified INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
        cursor = store._connection.execute('INSERT INTO feedback_resolutions '
            '(feedback_id,decision,record_json,evidence_json) VALUES(?,?,?,?)',
            (report_id,decision,json.dumps(row,ensure_ascii=False),json.dumps(evidence,ensure_ascii=False)))
        receipt = cursor.lastrowid
        store._connection.execute('UPDATE translation_feedback SET status=? WHERE id=?', (status,report_id))
    # Terminal first: Discord reaction-remove events must not erase open rows.
    # Any HTTP failure leaves a durable pending receipt for an explicit retry.
    flagged_ids = set(store.feedback_flag_messages(report_id)) | {row['translation_message_id'], *sources}
    for message_id in flagged_ids:
        if get(message_id) is not None:
            request('DELETE', f'/channels/{channel}/messages/{message_id}/reactions/{quote("🚩",safe="")}/{row["reporter_id"]}', allow_missing=True)
            users = request('GET', f'/channels/{channel}/messages/{message_id}/reactions/{quote("🚩",safe="")}?after={max(0,row["reporter_id"]-1)}&limit=1', allow_missing=True)
            if users and any(int(user['id']) == row['reporter_id'] for user in users):
                raise RuntimeError('Report closed but reaction removal is not yet verified; retry resolution.')
    with store._lock, store._connection:
        store._connection.execute('UPDATE feedback_resolutions SET cleanup_verified=1 WHERE id=?', (receipt,))
    return status
