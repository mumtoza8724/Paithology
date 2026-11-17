"""
Языки интерфейса PAITHOLOGY: русский (исходный), английский и узбекский
(латиница).

Исходные строки пишутся по-русски прямо в шаблонах и коде: {{ _("Главная") }}.
Переводы лежат в translations/en.json и translations/uz.json в виде
«русская строка → перевод». Если перевода нет, показывается русская строка.

Выбранный язык хранится в cookie (без изменений в базе данных). Тексты,
которые создаёт само приложение (описание ИИ-анализа, протоколы DOCX/PDF),
от языка интерфейса не зависят.
"""

import json
import os

from flask import g, has_request_context, request


LANGUAGES = (
    # код, подпись на кнопке, название языка на нём самом
    ("ru", "RU", "Русский"),
    ("en", "EN", "English"),
    ("uz", "UZ", "Oʻzbekcha"),
)

CODES = tuple(code for code, _short, _name in LANGUAGES)

DEFAULT_LANG = "ru"

COOKIE_NAME = "paithology_lang"

COOKIE_MAX_AGE = 60 * 60 * 24 * 365

TRANSLATIONS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "translations"
)


def load_catalogs():

    catalogs = {}

    for code in CODES:

        if code == DEFAULT_LANG:
            continue

        path = os.path.join(TRANSLATIONS_DIR, f"{code}.json")

        with open(path, encoding="utf-8") as f:
            catalogs[code] = json.load(f)

    return catalogs


CATALOGS = load_catalogs()


def normalize(code):

    code = (code or "").strip().lower()

    return code if code in CODES else None


def detect_language():
    """Язык из cookie, иначе из настроек браузера, иначе русский."""

    chosen = normalize(request.cookies.get(COOKIE_NAME))

    if chosen:
        return chosen

    best = request.accept_languages.best_match(CODES)

    return best or DEFAULT_LANG


def current_language():

    if not has_request_context():
        return DEFAULT_LANG

    lang = getattr(g, "lang", None)

    if lang is None:
        lang = detect_language()
        g.lang = lang

    return lang


def translate(text, lang=None):

    lang = lang or current_language()

    if lang == DEFAULT_LANG:
        return text

    return CATALOGS.get(lang, {}).get(text) or text


def gettext(text, **values):
    """Перевод строки; именованные значения подставляются через {имя}."""

    result = translate(text)

    if values:
        result = result.format(**values)

    return result


def script_catalog(lang):
    """Словарь для JavaScript: только переводы строк из скриптов."""

    if lang == DEFAULT_LANG:
        return {}

    catalog = CATALOGS.get(lang, {})

    return {key: catalog[key] for key in SCRIPT_MESSAGES if key in catalog}


def load_script_messages():

    path = os.path.join(TRANSLATIONS_DIR, "script_messages.json")

    with open(path, encoding="utf-8") as f:
        return tuple(json.load(f))


SCRIPT_MESSAGES = load_script_messages()


def safe_next(target):
    """Адрес возврата после смены языка: только путь внутри сайта."""

    if not target or not target.startswith("/") or target.startswith("//"):
        return "/"

    if "\\" in target or "\r" in target or "\n" in target:
        return "/"

    return target
