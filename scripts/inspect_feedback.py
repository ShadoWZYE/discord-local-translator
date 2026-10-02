"""Read live source boundaries for explicit feedback IDs; never print credentials."""
import sys
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8')
from translator_bot.config import Settings
from translator_bot.storage import SettingsStore

settings = Settings.load()
store = SettingsStore(settings.database_path)
with requests.Session() as session:
    session.headers['Authorization'] = 'Bot '+settings.discord_token
    for report_id in map(int, sys.argv[1].split(',')):
        report = store._connection.execute('SELECT * FROM translation_feedback WHERE id=?', (report_id,)).fetchone()
        if not report or report['guild_id'] not in settings.guild_ids:
            raise RuntimeError('Report missing or outside allowed servers.')
        print('REPORT', report_id)
        link = store.get_translation_link(report['translation_message_id'])
        for message_id in store.get_batch_sources(link['source_message_id']):
            response = session.get(f'https://discord.com/api/v10/channels/{report["channel_id"]}/messages/{message_id}', timeout=30)
            if response.status_code != 200:
                raise RuntimeError(f'Discord HTTP {response.status_code}')
            message = response.json()
            print('SOURCE', message_id, message['timestamp'], repr(message['content']),
                  'reply', message.get('message_reference'))
store.close()
