"""Inspect the review threshold without loading a model or changing bot tables."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from translator_bot.config import Settings
from translator_bot.maintenance import review_queue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--force', action='store_true', help='Ignore threshold and attempt suppression.')
    parser.add_argument('--record', help='Record attempted report IDs, comma-separated; does not approve or close reports.')
    parser.add_argument('--outcome', choices=('corrected_verified', 'needs_clarification', 'acceptable_variant', 'unavailable'))
    parser.add_argument('--fingerprints', help='Exact fingerprints from the inspected queue JSON; required for recording.')
    args = parser.parse_args()
    settings = Settings.load()
    state = ROOT / 'data' / 'maintenance' / 'review_attempts.json'
    attempts = json.loads(state.read_text(encoding='utf-8')) if state.exists() else {}
    with closing(sqlite3.connect(settings.database_path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute('SELECT * FROM translation_feedback')
                if r['guild_id'] in settings.guild_ids]
    if args.record:
        if not args.outcome or not args.fingerprints:
            parser.error('--record needs --outcome and --fingerprints from the pre-review queue')
        supplied = json.loads(args.fingerprints)
        ids = set(map(int, args.record.split(',')))
        selected = [r for r in rows if r['id'] in ids]
        if len(selected) != len(ids):
            parser.error('Unknown or out-of-scope report IDs.')
        for row in selected:
            # Bind suppression to the inspected version; a changed report stays eligible.
            expected = supplied.get(str(row['id']))
            if not isinstance(expected, str) or len(expected) != 64:
                parser.error('Missing inspected fingerprint for a report.')
            attempts[str(row['id'])] = {'fingerprint': expected,
                'outcome': args.outcome, 'attempted_at': datetime.now(timezone.utc).isoformat()}
        state.parent.mkdir(parents=True, exist_ok=True)
        temporary = state.with_suffix('.tmp')
        temporary.write_text(json.dumps(attempts, indent=2), encoding='utf-8')
        temporary.replace(state)
    print(json.dumps(review_queue(rows, attempts, force=args.force), indent=2))


if __name__ == '__main__':
    main()
