"""Read-only cache diagnostics: no model, source plaintext, seeding or writes."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from translator_bot.config import Settings
from translator_bot.result_cache import capacity_status
from translator_bot.reuse_store import ReuseStore
from types import SimpleNamespace


def report(path):
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result={'integrity':db.execute('PRAGMA quick_check').fetchone()[0],
            **capacity_status(db),
            'durable_approved_units':db.execute('SELECT COUNT(*) FROM translation_memory').fetchone()[0] if 'translation_memory' in tables else 0,
            'global_clear_revision':db.execute('SELECT revision FROM cache_revision WHERE id=1').fetchone()[0],
            'sample':'Inference-path fragment observations, NOT live delivery hit percentages or measured time savings.'}
        if 'fragment_observations' in tables:
            db.row_factory=sqlite3.Row
            result['repeated_miss_groups']=[dict(r) for r in db.execute(
                'SELECT hex(unit) AS unit_hash,lookups,hits,misses,repeated_misses,eligible_repeated_misses,'
                'changed_scope_misses,reason,characters,last_seen FROM fragment_observations '
                'WHERE repeated_misses>0 ORDER BY eligible_repeated_misses DESC,repeated_misses DESC LIMIT 50')]
        if 'request_fragment_observations' in tables:
            db.row_factory=sqlite3.Row
            result['request_observations_by_origin']=[dict(r) for r in db.execute(
                'SELECT origin,COUNT(*) AS unit_versions,SUM(attempts) AS attempts,'
                'COUNT(DISTINCT message) AS distinct_messages,SUM(hit) AS reused_unit_versions '
                'FROM request_fragment_observations GROUP BY origin')]
            result['live_repeated_units']=[dict(r) for r in db.execute(
                'SELECT hex(unit) AS unit_hash,COUNT(DISTINCT message) AS distinct_messages,'
                'COUNT(*) AS versions,COUNT(DISTINCT identity) AS scopes,MAX(eligible) AS structurally_eligible '
                "FROM request_fragment_observations WHERE origin='automatic' GROUP BY unit "
                'HAVING COUNT(DISTINCT message)>1 ORDER BY distinct_messages DESC LIMIT 50')]
            result['request_observation_note']='Version/attempt counts include edits; live repetitions require distinct source messages. Legacy observations include manual work and failed attempts.'
        if 'reuse_rules' in tables:
            result['recent_live_cache']=ReuseStore(SimpleNamespace(_connect=lambda:sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True))).recent_summary()
            result['reviewed_reuse_rules']=db.execute('SELECT COUNT(*) FROM reuse_rules').fetchone()[0]
            result['reuse_approval_receipts']=db.execute('SELECT COUNT(*) FROM reuse_approvals').fetchone()[0]
            result['reuse_request_audit_rows']=db.execute('SELECT COUNT(*) FROM reuse_requests').fetchone()[0]
            result['reuse_pattern_report']='scripts/reuse_report.py; bounded source-only candidates, never automatic approval'
        return result


if __name__=='__main__':
    print(json.dumps(report(Settings.load().database_path.with_suffix('.cache.sqlite3')),indent=2))
