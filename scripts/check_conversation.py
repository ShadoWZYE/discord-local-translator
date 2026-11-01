"""Synthetic live semantic regressions and paired timing; no Discord writes.

Not a general quality score: required terms are narrow diagnostics; inspect the
printed full translations too. Add synthetic cases derived from reviewed flags,
never publish private conversations in this file.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from translator_bot.config import Settings
from translator_bot.translation import LocalTranslator

CASES = [
    ('explicit body ownership', 'ru_to_en', 'Моя шея болит.',
        'How are you feeling?', ('my neck',), ('your neck', 'his neck', 'her neck')),
    ('generic person example', 'ru_to_en',
        'Люди иногда делают выводы без причины. Тебе просто не нравится человек.',
        'We are discussing why people sometimes dislike others in general, not who I personally dislike.',
        ('dislike', 'like'), ('I dislike', 'I do not like')),
    ('omitted actor across action clauses', 'ru_to_en',
        '*Я повернулась к нему. Отпустив плечо, свободной рукой усадила тебя на диван и склонилась над тобой.*',
        'We are describing what I did.', ('I',), ('he sat', 'he leaned', 'his free hand')),
    ('short corrective noun fragment', 'en_to_ru', 'A room, really...',
        'You mean you live in a palace?', ('комнат',), ('очень',)),
    ('first-person body after listener reference', 'ru_to_en',
        'Я чувствую тепло твоей руки. Тело расслабляется, шея и плечи перестают болеть.',
        'I have been tense and sore today.', ('my body', 'my neck', 'my shoulders'), ('your body', 'your neck', 'your shoulders')),
    ('stretched sympathetic interjection', 'en_to_ru', 'Owww... Poor thing...',
        'I slept badly and woke up with a sore neck.', ('бед',), ('www',)),
    ('option-selecting short reply', 'ru_to_en', 'Что всё нужно делать самому.',
        'Which is the problem: being right or believing you have to do everything yourself?',
        ('that', 'idea', 'belief'), ()),
    ('supportive venting invitation', 'ru_to_en', 'Раз накопилось, то выговаривай всё.',
        "I'm frustrated and upset. Sorry for dumping my feelings on you.",
        ('out', 'off your chest'), ('pronounc',)),
    ('inline command names', 'en_to_ru', '<@123> When the bot acts oddly, use /fix or -fix to reconnect.',
        'The music bot sometimes disconnects.', ('/fix',), ('/почин',)),
    ('donation not sacrifice', 'ru_to_en', 'Не жертва! Пожертвовать!',
        'We unlock the game perk by donating money. Nobody is being sacrificed.', ('donat',), ('victim',)),
    ('only is not also', 'ru_to_en', 'Всего лишь игрушку спрятала 🤭',
        'Did you hide anything else?', ('only', 'just'), ('also', 'secondary', '😉')),
    ('outcome and parenthetical aside', 'ru_to_en',
        'Нужно съесть что-нибудь сладкое и отпустит (надеюсь, никто не успеет вывести меня из себя 🙈)',
        'My mood has been swinging today.', ('better', 'pass', 'calm', 'go away', 'ease'), ('talking',)),
    ('light means low weight', 'en_to_ru', 'It is light.',
        'We weighed the suitcase. It weighs only two kilograms.', ('лёгк', 'легк'), ('светл',)),
    ('light means brightness', 'en_to_ru', 'It is light.',
        'The room receives lots of sunlight through the windows.', ('светл',), ('лёгк', 'легк')),
    ('inanimate pronoun', 'ru_to_en', 'Она сломалась.', 'Это моя клавиатура.', ('it',), ('she',)),
    ('short reply idiom', 'en_to_ru', "I'm down.", 'Can you help repair the server?', ('помог', 'соглас', 'готов'), ('внизу',)),
    ('named player and rank', 'ru_to_en', 'Он теперь R4.', 'Игрок Nova получил повышение.', ('R4',), ()),
    ('bounded larger context', 'ru_to_en', 'Я проверю её завтра. ~ Пока не работает.',
        'Это моя клавиатура. Она подключена к компьютеру. ' +
        'Мы обсуждаем клавиатуру и проверяем соединение с компьютером. '*12,
        ('it',), ('her',)),
]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    settings = Settings.load()
    translator = LocalTranslator(settings.ollama_url, settings.ollama_model,
        settings.ollama_timeout_seconds)
    # Warm once; exclude model-loading time from the paired measurements.
    translator.translate('Hello.', 'en_to_ru')
    times = {'baseline': [], 'context': []}
    failures = []
    for name, mode, source, previous, required, forbidden in CASES:
        context = json.dumps({'current_speaker': {'id': '2', 'name': 'Alex'},
            'originals': [{'speaker': {'id': '1', 'name': 'Sam'},
                'reply_target': True, 'text': previous}]}, ensure_ascii=False)
        for label, reference in [('baseline', ''), ('context', context)]:
            started = time.perf_counter()
            with translator.conversation_context(reference):
                result = translator.translate(source, mode)
            elapsed = time.perf_counter() - started
            times[label].append(elapsed)
            output = result.text if result else ''
            print(f'{name} | {label} | {elapsed:.2f}s | {output}', flush=True)
            if label == 'context' and (not any(term.casefold() in output.casefold() for term in required)
                    or any(term.casefold() in output.casefold() for term in forbidden)):
                failures.append(name)
    for label, samples in times.items():
        print(f'{label} mean: {sum(samples)/len(samples):.2f}s', flush=True)
    print('Semantic diagnostic failures:', failures, flush=True)
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
