"""Cheap, read-only review gate. Attempt receipts are NOT linguistic approvals."""
from datetime import datetime, timezone
import hashlib
import json

PRIORITY_GUILD = 1554947765489107135


def fingerprint(row):
    fields = ('id', 'source_text', 'translated_text', 'corrected_text', 'status')
    value = json.dumps([row.get(key) for key in fields], ensure_ascii=False)
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def review_queue(rows, attempts, *, now=None, force=False):
    """Three distinct posts, or one waiting 24h; skip unchanged reviewed attempts."""
    now = now or datetime.now(timezone.utc)
    rows = [dict(row) for row in rows]
    pending = [r for r in rows if r['status'] in ('open', 'reviewed')]
    eligible = [r for r in pending if force or
                attempts.get(str(r['id']), {}).get('fingerprint') != fingerprint(r)]
    # Multiple reporters of one webhook must not trip the three-post threshold.
    groups = {}
    for row in eligible:
        key = (row['guild_id'], row['channel_id'], row['translation_message_id'])
        groups.setdefault(key, []).append(row)
    ordered = sorted(groups.values(), key=lambda group: (
        group[0]['guild_id'] != PRIORITY_GUILD,
        min(r['created_at'] for r in group), min(r['id'] for r in group)))
    oldest = max((max(0, (now - datetime.fromisoformat(
        r['created_at']).replace(tzinfo=timezone.utc)).total_seconds())
        for r in eligible), default=0)
    ready = bool(ordered) and (force or len(ordered) >= 3 or oldest >= 86400)
    return {
        'due': ready,
        'reason': ('manual' if force and ordered else 'three_distinct_posts'
                   if len(ordered) >= 3 else 'oldest_over_24h' if ready else 'below_threshold'),
        'pending_reports': len(pending), 'eligible_posts': len(ordered),
        'unchanged_attempts_skipped': len(pending) - len(eligible),
        'oldest_eligible_hours': round(oldest / 3600, 2),
        'groups': [{'report_ids': [r['id'] for r in group],
                    'guild_id': group[0]['guild_id'],
                    'channel_id': group[0]['channel_id'],
                    'translation_message_id': group[0]['translation_message_id'],
                    'fingerprints': {str(r['id']): fingerprint(r) for r in group}}
                   for group in ordered],
    }
