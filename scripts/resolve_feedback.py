"""Close reviewed-no-change/obsolete reports using exact private evidence.

Usage: resolve_feedback.py 12,13 data/resolutions.json
Failures leave a durable receipt if closure happened before reaction cleanup.
Retry with a fresh report fingerprint. No translation text/cache is changed.
"""
import json
from pathlib import Path
import sys
import time
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from translator_bot.config import Settings
from translator_bot.storage import SettingsStore
from translator_bot.feedback_resolution import resolve_report


def main():
    path = Path(sys.argv[2]).resolve()
    if not path.is_relative_to(ROOT / 'data'):
        raise ValueError('Private resolution evidence belongs in ignored data/.')
    records = json.loads(path.read_text(encoding='utf-8'))
    settings = Settings.load()
    store = SettingsStore(settings.database_path)
    session = requests.Session()
    session.headers['Authorization'] = 'Bot ' + settings.discord_token

    def request(method, path, *, allow_missing=False):
        for attempt in range(4):
            try:
                response = session.request(method, 'https://discord.com/api/v10' + path, timeout=30)
            except requests.RequestException:
                raise RuntimeError('Discord request failed; credentials suppressed.') from None
            if response.status_code != 429:
                break
            delay = float(response.json().get('retry_after', 1))
            if delay > 30 or attempt == 3:
                raise RuntimeError('Discord rate limit; retry later.')
            time.sleep(max(0, delay) + .1)
        if allow_missing and response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise RuntimeError(f'Discord HTTP {response.status_code}; no secret URLs logged.')
        return response.json() if response.content else None

    try:
        for report_id in map(int, sys.argv[1].split(',')):
            status = resolve_report(store, report_id, records[str(report_id)], request, settings.guild_ids)
            print('CLOSED; FLAGS VERIFIED', report_id, status, flush=True)
    finally:
        store.close()
        session.close()


if __name__ == '__main__':
    main()
