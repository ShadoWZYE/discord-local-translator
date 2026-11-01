"""Conservative EN/RU name rendering, without inference or guessed identities."""
import re

# Conventional spellings, not cultural equivalents (John is NOT Ivan).
PAIRS = (
    ('Alice', 'Алиса'), ('Angelina', 'Ангелина'), ('Anna', 'Анна'), ('Adda','Адда'),
    ('Anya', 'Аня'), ('Alex', 'Алекс'), ('Alexander', 'Александр'),
    ('John', 'Джон'), ('James', 'Джеймс'), ('Julia', 'Джулия'),
    ('Maria', 'Мария'), ('Michael', 'Майкл'), ('Daniel', 'Дэниел'),
    ('David', 'Дэвид'), ('Joseph', 'Джозеф'), ('Nicholas', 'Николас'),
    ('Sophie', 'Софи'), ('Emma', 'Эмма'), ('Olivia', 'Оливия'),
    ('Thomas', 'Томас'), ('Robert', 'Роберт'), ('Peter', 'Питер'),
    ('Dmitry', 'Дмитрий'), ('Ivan', 'Иван'), ('Sergey', 'Сергей'),
    ('Natalia', 'Наталья'), ('Elena', 'Елена'), ('Olga', 'Ольга'),
    ('Yulia', 'Юлия'), ('Mikhail', 'Михаил'), ('Nikolai', 'Николай'),
)
COMPONENTS = (
    ('Lady', 'Леди'), ('Lord', 'Лорд'), ('Chaos', 'Хаос'), ('Goblin', 'Гоблин'),
    ('Shadow', 'Тень'), ('Fox', 'Лис'), ('Wolf', 'Волк'), ('North', 'Север'),
    ('Captain', 'Капитан'), ('Dragon', 'Дракон'), ('Knight', 'Рыцарь'),
    ('King', 'Король'), ('Queen', 'Королева'), ('Fire', 'Огонь'),
    ('Frost', 'Мороз'), ('Moon', 'Луна'), ('Dark', 'Тёмный'),
    ('Black', 'Чёрный'), ('White', 'Белый'), ('Red', 'Красный'),
    ('Gros', 'Грос'),
)
CAMEL_PATTERN = r'(?-i:(?<!\w)(?:[A-Z][a-z]+(?:[A-Z][a-z]+)+|[А-ЯЁ][а-яё]+(?:[А-ЯЁ][а-яё]+)+)(?!\w))'


def inflected_aliases():
    """Closed known names, not arbitrary noun stemming or identity guesses."""
    aliases = {}
    for english, russian in PAIRS:
        stem = russian[:-1]
        if russian.endswith('а'):
            endings = ('у','е','ой','ою','и' if stem[-1] in 'гкхжчшщ' else 'ы')
        elif russian.endswith('я'):
            endings = ('и','ю','ей','ею') if russian.endswith('ия') else ('и','е','ю','ей','ею')
        else:
            continue
        for ending in endings:
            aliases[stem+ending] = english
    return aliases


def localized_name(name, source, target):
    if (source, target) not in (('en', 'ru'), ('ru', 'en')):
        return None
    reverse = source == 'ru'
    aliases = {b if reverse else a: a if reverse else b for a, b in PAIRS}
    if reverse:
        aliases.update(inflected_aliases())
    if name in aliases:
        return aliases[name]
    parts = re.findall(r'[A-Z][a-z]+|[А-ЯЁ][а-яё]+', name)
    # A single ordinary word (e.g. Lady, Shadow) is not a nickname by itself.
    # All compound components must be recognized; no partial/fuzzy rendering.
    if len(parts) < 2 or ''.join(parts) != name:
        return None
    components = {b if reverse else a: a if reverse else b for a, b in (*COMPONENTS, *PAIRS)}
    if not all(part in components for part in parts):
        return None
    return ''.join(components[part] for part in parts)


def name_bindings(text, source, target, fixed_pattern):
    """Recognize whole names only, excluding code, links, mentions and tags."""
    fixed = list(fixed_pattern.finditer(text))
    bindings = {}
    for match in re.finditer(r'(?<![\w@#.-])([A-Za-zА-Яа-яЁё]+)(?![\w#-]|\.[A-Za-z0-9])', text):
        name = match.group()
        # Camel-case identifiers may be rendered; other fixed spans may not.
        if any(m.start() <= match.start() < m.end()
               and not re.fullmatch(CAMEL_PATTERN, m.group()) for m in fixed):
            continue
        rendered = localized_name(name, source, target)
        if rendered is not None:
            bindings[name] = rendered
    return bindings


def name_pattern(fixed_pattern, bindings):
    if not bindings:
        return fixed_pattern
    names = '|'.join(re.escape(name) for name in sorted(bindings, key=len, reverse=True))
    return re.compile(fixed_pattern.pattern + r'|(?-i:(?<![\w@#.-])(?:' + names + r')(?![\w#-]|\.[A-Za-z0-9]))', re.I)


def standalone_name_language(text, languages):
    match = re.fullmatch(r'[\s.!?…~]*([A-Za-zА-Яа-яЁё]+)[\s.!?…~]*', text)
    if not match:
        return None
    for source, target in (('en', 'ru'), ('ru', 'en')):
        if source in languages and localized_name(match.group(1), source, target):
            return source
    return None
