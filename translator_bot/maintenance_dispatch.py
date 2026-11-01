"""Token-free dispatch decisions. Never import translation/model modules here."""
import hashlib
import json
from datetime import datetime, timezone


def dispatch_signature(queue, alerts):
    payload = {'groups': queue['groups'][:10] if queue['due'] else [], 'alerts': alerts,
               'runner_contract':'interactive-handoff-receipts-v1'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def dispatch_decision(queue, state, *, alerts=(), now=None, force=False):
    now = now or datetime.now(timezone.utc)
    signature = dispatch_signature(queue, alerts)
    result = {'launch': False, 'signature': signature, 'reason': 'nothing_due'}
    if not (queue['due'] or alerts or force):
        return result
    previous = state.get('jobs', {}).get(signature, {})
    if previous and not force:
        if previous.get('exit_code') == 0 and previous.get('completion_verified') is True:
            return dict(result, reason='unchanged_completed_job')
        if previous.get('attempts', 0) >= 3:
            return dict(result, reason='retry_limit_needs_attention')
        if previous.get('exit_code')==0 and 'completion_verified' not in previous:
            # Earlier runners recorded successful CLI exits without proving
            # any command/review happened. Allow one upgrade recovery attempt;
            # subsequent failures use the normal bounded backoff.
            return dict(result,launch=True,reason='unverified_legacy_job')
        started = datetime.fromisoformat(previous['started_at']) if previous.get('started_at') else None
        if started is not None and (now - started).total_seconds() < 1800:
            return dict(result, reason='failure_backoff')
    return dict(result, launch=True, reason='manual' if force else 'flags_due' if queue['due'] else 'health_alert')
