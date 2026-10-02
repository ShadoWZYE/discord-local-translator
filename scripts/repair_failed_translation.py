"""Repair one existing failure notice and recover its previously ignored flags.

Only message IDs are supplied on the command line. Discord credentials and webhook
tokens stay in memory and are never printed. No new public message is created.
"""
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

import requests
from translator_bot.config import Settings
from translator_bot.storage import SettingsStore
from translator_bot.translation import LocalTranslator


def main():
    source_id = int(sys.argv[1])
    settings = Settings.load()
    store = SettingsStore(settings.database_path)
    session = requests.Session()
    session.headers['Authorization'] = 'Bot ' + settings.discord_token

    def request(method, path, **kwargs):
        try:
            response = session.request(method, 'https://discord.com/api/v10' + path, timeout=30, **kwargs)
        except requests.RequestException:
            raise RuntimeError('Discord request failed; credentials are omitted from diagnostics.') from None
        if response.status_code >= 400:
            raise RuntimeError(f'Discord request failed with HTTP {response.status_code}.')
        return response.json() if response.content else None

    try:
        links = store.get_source_links(source_id)
        if len(links) != 1 or links[0]['status'] != 'failed':
            raise RuntimeError('Expected exactly one existing failed translation; no changes made.')
        link = links[0]
        channel_id = link['channel_id']
        channel = request('GET', f'/channels/{channel_id}')
        guild_id = int(channel['guild_id'])
        if guild_id not in settings.guild_ids:
            raise RuntimeError('Server is outside the bot allowlist.')
        source_ids = store.get_batch_sources(link['source_message_id'])
        originals = [request('GET', f'/channels/{channel_id}/messages/{message_id}') for message_id in source_ids]
        failed_id = link['translation_message_id']
        notice = request('GET', f'/channels/{channel_id}/messages/{failed_id}')
        if int(notice.get('webhook_id', 0)) != link['webhook_id']:
            raise RuntimeError('The failure notice does not belong to the expected webhook.')
        reported = any(item['emoji'].get('name') == '🚩' for item in notice.get('reactions', []))
        if reported:
            reporters = request('GET', f'/channels/{channel_id}/messages/{failed_id}/reactions/{quote("🚩")}?limit=100')
            for reporter in reporters:
                if not reporter.get('bot', False):
                    store.add_feedback(
                        guild_id=guild_id, channel_id=channel_id, source_message_id=link['source_message_id'],
                        translation_message_id=failed_id, source_author_id=int(originals[0]['author']['id']),
                        reporter_id=int(reporter['id']), source_language=link['source_language'],
                        target_language=link['target_language'],
                        source_text='\n'.join(message['content'] for message in originals),
                        translated_text=notice['content'], corrected_text=None,
                        note='Automatic translation failed because the model omitted protected Discord mentions.',
                    )
        text = ' '.join(message['content'] for message in originals)
        translator = LocalTranslator(settings.ollama_url, settings.ollama_model, settings.ollama_timeout_seconds)
        result = translator.translate_to(text, link['target_language'], link['source_language'])
        if result is None or len(result.text) > 1900:
            raise RuntimeError('Repair did not yield one validated Discord-sized translation.')
        current = [request('GET', f'/channels/{channel_id}/messages/{message_id}') for message_id in source_ids]
        if [message['content'] for message in current] != [message['content'] for message in originals]:
            raise RuntimeError('The original changed during repair; no stale translation published.')
        webhook = request('GET', f'/webhooks/{link["webhook_id"]}')
        token = webhook.get('token')
        if not token:
            raise RuntimeError('The original webhook credentials are unavailable.')
        request('PATCH', f'/webhooks/{link["webhook_id"]}/{token}/messages/{failed_id}',
                json={'content': result.text, 'allowed_mentions': {'parse': []}})
        store.link_translation(failed_id, link['source_message_id'], channel_id, link['webhook_id'],
                               result.source, result.target, status='complete')
        with store._connection:
            store._connection.execute(
                "UPDATE translation_feedback SET status='applied', corrected_text=?, "
                "note=COALESCE(note, '') || ' Fixed with validated full-source syntax retry.' "
                "WHERE translation_message_id=? AND status='open'", (result.text, failed_id),
            )
        print('Repaired the existing translation in place:', failed_id)
        print('Existing failure flags recovered and marked applied:', reported)
        print('TRANSLATION:', result.text)
    finally:
        store.close()
        session.close()


if __name__ == '__main__':
    main()
