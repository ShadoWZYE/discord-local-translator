"""Live local-model checks for fragmented sentences and deliberate line breaks."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from translator_bot.config import Settings
from translator_bot.translation import LocalTranslator


def main():
    settings = Settings.load()
    translator = LocalTranslator(settings.ollama_url, settings.ollama_model, settings.ollama_timeout_seconds)
    cases = [
        ("en_to_ru", ["Do not cancel the order", "until the supplier confirms", "that the replacement is available."]),
        ("en_to_ru", ["I'm down", "to help with the server", "but not tonight."]),
        ("ru_to_en", ["Давай пока не", "будем удалять сообщения,", "сначала проверим перевод."]),
        ("ru_to_en", ["Мне не всё равно,", "просто я не знаю,", "как тебе помочь."]),
    ]
    for mode, fragments in cases:
        print("SOURCE:", " | ".join(fragments), flush=True)
        print("NEWLINES:", translator.translate("\n".join(fragments), mode).text, flush=True)
        print("CONTINUOUS:", translator.translate(" ".join(fragments), mode).text, flush=True)
    text = "Deployment plan:\n1. Check the translation.\n2. Restart the bot."
    print("MULTILINE SOURCE:", text, flush=True)
    print("MULTILINE RESULT:", translator.translate(text, "en_to_ru").text, flush=True)
    for mode, text in [
        ('en_to_ru', 'Holy shit! This fucking bot actually works. 😂'),
        ('en_to_ru', 'Yeah, another fucking brilliant idea. 🙄'),
        ('ru_to_en', 'Блядь, ну охуенно, опять всё сломалось. 🙄'),
        ('en_to_ru', 'Ask <@123> to check this: https://example.com'),
        ('en_to_ru', 'This^ is funny. ~Well, maybe not. 😂'),
    ]:
        print('TONE/SYNTAX SOURCE:', text, flush=True)
        print('TONE/SYNTAX RESULT:', translator.translate(text, mode).text, flush=True)


if __name__ == "__main__":
    main()
