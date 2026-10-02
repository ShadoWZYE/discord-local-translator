"""Live regression checks for tilde reconstruction and multiline context."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8')
from translator_bot.config import Settings
from translator_bot.translation import LocalTranslator

s = Settings.load()
t = LocalTranslator(s.ollama_url, s.ollama_model, s.ollama_timeout_seconds)
cases = [
    ('en', 'ru', 'Yes. Otherwise you only get the white dot. For ~ "new messages" ~'),
    ('en', 'ru', 'Yes. The last options are cow milk ~ Whole / Semi-skimmed / Skimmed (milk, cow, specifically)'),
    ('en', 'ru', 'Perhaps a bug. ~ Oh well.\nLet us continue. ~'),
    ('en', 'ru', 'Ask <@123> ~ check `a~b` at https://example.com/~me ~ then reply.'),
    ('ru', 'en', 'Я хочу ~ чтобы это работало.\nНу что ж. ~ Продолжим.))'),
    ('en', 'ru', 'Wait ~ ~ what?'),
]
for source, target, text in cases:
    result = t.translate_to(text, target, source)
    assert result is not None
    assert result.text.count('~') == text.count('~'), (text, result.text)
    assert result.text.count('\n') == text.count('\n'), (text, result.text)
    print('PASS', repr(text), '=>', repr(result.text), flush=True)
