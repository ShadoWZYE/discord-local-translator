"""Replay open feedback through the local model without changing stored records."""
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from translator_bot.config import Settings
from translator_bot.translation import LocalTranslator


def main():
    settings = Settings.load()
    with sqlite3.connect(f"{settings.database_path.as_uri()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        records = db.execute(
            "SELECT id, source_language, target_language, source_text, translated_text "
            "FROM translation_feedback WHERE status = 'open' ORDER BY id"
        ).fetchall()
    translator = LocalTranslator(settings.ollama_url, settings.ollama_model, settings.ollama_timeout_seconds)
    for record in records:
        started = time.perf_counter()
        print(f'RECORD {record["id"]}', flush=True)
        print("SOURCE:", record["source_text"], flush=True)
        print("PREVIOUS:", record["translated_text"], flush=True)
        try:
            result = translator.translate_to(record["source_text"], record["target_language"], record["source_language"])
            print("REPLAY:", result.text if result else "NO TRANSLATION", flush=True)
        except Exception as exc:
            print("REPLAY FAILED:", type(exc).__name__, str(exc), flush=True)
        print("SECONDS:", round(time.perf_counter() - started, 2), flush=True)


if __name__ == "__main__":
    main()
