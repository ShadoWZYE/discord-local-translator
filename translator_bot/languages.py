"""Explicit language/flag choices; flags are shortcuts, not language detection."""
import math
import re

LANGUAGES = {
    'en': 'English', 'ru': 'Russian', 'ro': 'Romanian', 'fr': 'French',
    'de': 'German', 'es': 'Spanish', 'it': 'Italian', 'pt': 'Portuguese',
    'nl': 'Dutch', 'pl': 'Polish', 'uk': 'Ukrainian', 'tr': 'Turkish',
    'cs': 'Czech', 'el': 'Greek', 'sv': 'Swedish', 'fi': 'Finnish',
    'hu': 'Hungarian', 'bg': 'Bulgarian', 'ar': 'Arabic', 'he': 'Hebrew',
    'ja': 'Japanese', 'ko': 'Korean', 'zh': 'Chinese (Simplified)',
}
DEFAULT_LANGUAGES = ('en', 'ru')
TRANSLATION_ERRORS = {
    'en': 'Translation unavailable. Edit your message to retry.',
    'ru': 'Перевод недоступен. Отредактируйте сообщение, чтобы повторить.',
    'ro': 'Traducerea nu este disponibilă. Editează mesajul pentru a încerca din nou.',
    'fr': 'Traduction indisponible. Modifiez votre message pour réessayer.',
    'de': 'Übersetzung nicht verfügbar. Bearbeite deine Nachricht, um es erneut zu versuchen.',
    'es': 'Traducción no disponible. Edita tu mensaje para volver a intentarlo.',
    'it': 'Traduzione non disponibile. Modifica il messaggio per riprovare.',
    'pt': 'Tradução indisponível. Edite sua mensagem para tentar novamente.',
    'nl': 'Vertaling niet beschikbaar. Bewerk je bericht om het opnieuw te proberen.',
    'pl': 'Tłumaczenie niedostępne. Edytuj wiadomość, aby spróbować ponownie.',
    'uk': 'Переклад недоступний. Відредагуйте повідомлення, щоб спробувати ще раз.',
    'tr': 'Çeviri kullanılamıyor. Yeniden denemek için mesajınızı düzenleyin.',
    'cs': 'Překlad není dostupný. Upravte zprávu a zkuste to znovu.',
    'el': 'Η μετάφραση δεν είναι διαθέσιμη. Επεξεργαστείτε το μήνυμά σας για να δοκιμάσετε ξανά.',
    'sv': 'Översättning inte tillgänglig. Redigera ditt meddelande för att försöka igen.',
    'fi': 'Käännös ei ole saatavilla. Muokkaa viestiäsi ja yritä uudelleen.',
    'hu': 'A fordítás nem érhető el. Szerkeszd az üzenetedet az újrapróbálkozáshoz.',
    'bg': 'Преводът не е наличен. Редактирайте съобщението си, за да опитате отново.',
    'ar': 'الترجمة غير متاحة. عدّل رسالتك للمحاولة مرة أخرى.',
    'he': 'התרגום אינו זמין. ערכו את ההודעה כדי לנסות שוב.',
    'ja': '翻訳できませんでした。メッセージを編集して再試行してください。',
    'ko': '번역할 수 없습니다. 메시지를 수정하여 다시 시도하세요.',
    'zh': '翻译暂不可用。请编辑消息后重试。',
}
# Explicit country/territory defaults, based on Unicode CLDR release 47
# territoryInfo: most spoken national official/de-facto official language,
# otherwise most spoken language. Only the existing supported languages route.
# Multilingual countries have a default, not an assertion of a single language.
# https://github.com/unicode-org/cldr/blob/release-47/common/supplemental/supplementalData.xml
COUNTRY_CODES_BY_LANGUAGE = {
    'en': 'AC AG AI AU BB BM BS BW BZ CA CC CK CQ CX DG DM FJ FK FM GB GD GG GH GI GM GS GU GY IE IM IO JE JM KI KN KY LC LR MH MP MS MW NA NF NG NR NU NZ PH PN SB SG SH SL SS SX SZ TA TC TT UM US VC VG VI ZA ZM',
    'ru': 'KZ RU',
    'ro': 'MD RO',
    'fr': 'BF BJ BL CD CG CI CM DJ FR GA GF GN GP LU MC MF ML MQ MU NC NE PF PM RE SC TF TG WF YT',
    'de': 'AT CH DE LI',
    'es': 'AR BO CL CO CR CU DO EA EC ES GQ GT HN IC MX NI PA PE PR SV UY VE',
    'it': 'IT SM VA',
    'pt': 'AO BR CV GW MZ PT ST TL',
    'nl': 'AW BE BQ NL SR',
    'pl': 'PL', 'uk': 'UA', 'tr': 'TR', 'cs': 'CZ', 'el': 'CY GR',
    'sv': 'AX SE', 'fi': 'FI', 'hu': 'HU', 'bg': 'BG',
    'ar': 'AE BH DZ EG EH IQ JO KM KW LB LY MA MR OM PS QA SA SD SY TD TN YE',
    'he': 'IL', 'ja': 'JP', 'ko': 'KP KR', 'zh': 'CN HK MO TW',
}


def country_flag(code: str) -> str:
    """Construct the actual Unicode regional-indicator emoji, not a shortcode."""
    if len(code) != 2 or not all('A' <= char <= 'Z' for char in code):
        raise ValueError('Expected a two-letter uppercase territory code.')
    return ''.join(chr(0x1F1E6 + ord(char) - ord('A')) for char in code)


FLAG_LANGUAGES = {
    country_flag(country): language
    for language, countries in COUNTRY_CODES_BY_LANGUAGE.items()
    for country in countries.split()
}
# Discord's England/Scotland/Wales are distinct tag-sequence emoji, not GB
# regional indicators or a plain black flag. English is their chosen default;
# Welsh and Scottish Gaelic are not currently supported translation targets.
SUBDIVISION_FLAGS = {
    name: '\U0001f3f4' + ''.join(chr(0xE0000 + ord(char)) for char in code) + '\U000e007f'
    for name, code in {'England': 'gbeng', 'Scotland': 'gbsct', 'Wales': 'gbwls'}.items()
}
FLAG_LANGUAGES.update({flag: 'en' for flag in SUBDIVISION_FLAGS.values()})


def parse_languages(value: str) -> tuple[str, ...]:
    names = {name.casefold(): code for code, name in LANGUAGES.items()}
    codes = tuple(dict.fromkeys(names.get(part.strip().casefold(), part.strip().lower())
                               for part in value.split(',') if part.strip()))
    if len(codes) < 2 or any(code not in LANGUAGES for code in codes):
        raise ValueError('Choose at least two supported languages, e.g. en,ru or en,ru,ro.')
    return codes


def reading_seconds(text: str) -> int:
    """100 wpm + 30s; at least 90s. CJK characters need their own allowance."""
    cjk = sum('\u3400' <= char <= '\u9fff' or '\u3040' <= char <= '\u30ff'
              or '\uac00' <= char <= '\ud7af' for char in text)
    other = ''.join(char for char in text if not ('\u3400' <= char <= '\u9fff'
                    or '\u3040' <= char <= '\u30ff' or '\uac00' <= char <= '\ud7af'))
    units = max(len(re.findall(r'\w+', other)), sum(char.isalpha() for char in other) / 4) + cjk / 2
    return max(90, math.ceil(30 + units * 60 / 100))
