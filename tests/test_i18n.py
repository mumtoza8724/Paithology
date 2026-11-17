"""
Три языка интерфейса: русский, английский и узбекский (латиница).

Проверяются переключатель и cookie, безопасный адрес возврата, полнота
переводов (каждая строка шаблонов, сервера и скриптов есть в en и uz) и
перевод сообщений сервера. Тексты документов и ИИ-анализа от языка
интерфейса не зависят.
"""

import glob
import json
import os
import re

import pytest

import i18n
import protocols

from conftest import ROOT, create_user, get_csrf, login


CYRILLIC = re.compile(r"[А-Яа-яЁё]")

MSGID = re.compile(r'_\(\s*"((?:[^"\\]|\\.)*)"')

SCRIPT_MSGID = re.compile(r'\bt\(\s*"((?:[^"\\]|\\.)*)"')

# Примеры значений, которые попадают в русский документ (их вводят по-русски)
DOCUMENT_EXAMPLES = (
    "Протоковый инвазивный рак", "белесоватого", "мягкой", "1,0х0,3 см",
    "2604 р", "1967 г", "58 лет", "Консультант, д.м.н.", "Тестова Мария",
)


def load(code):

    with open(os.path.join(ROOT, "translations", f"{code}.json"), encoding="utf-8") as f:
        return json.load(f)


def source_messages():

    found = set()

    for path in glob.glob(os.path.join(ROOT, "templates", "*.html")) + [os.path.join(ROOT, "app.py")]:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        found.update(m.replace('\\"', '"') for m in MSGID.findall(text))

    with open(os.path.join(ROOT, "protocols.py"), encoding="utf-8") as f:
        found.update(re.findall(r'ProtocolError\(\s*"([^"]+)"', f.read()))

    return found


def script_messages():

    found = set()

    paths = glob.glob(os.path.join(ROOT, "static", "*.js")) + glob.glob(os.path.join(ROOT, "templates", "*.html"))

    for path in paths:
        with open(path, encoding="utf-8") as f:
            found.update(SCRIPT_MSGID.findall(f.read()))

    return {m for m in found if CYRILLIC.search(m)}


@pytest.fixture
def doctor(app, client):

    create_user(app, "doctor", "doctor-pass")
    login(client, "doctor", "doctor-pass")

    return client


# ---------- Переводы ----------

def test_catalogs_have_same_keys_and_placeholders():

    en, uz = load("en"), load("uz")

    assert set(en) == set(uz)

    for key in en:
        assert en[key].strip() and uz[key].strip(), key
        names = set(re.findall(r"\{\w+\}", key))
        assert set(re.findall(r"\{\w+\}", en[key])) == names, key
        assert set(re.findall(r"\{\w+\}", uz[key])) == names, key


def test_translations_are_not_russian():

    for code in ("en", "uz"):
        for key, value in load(code).items():
            if CYRILLIC.search(value):
                assert any(example in value for example in DOCUMENT_EXAMPLES), (code, key, value)


def test_every_interface_string_is_translated():

    en, uz = load("en"), load("uz")

    # строки-префиксы вида _("Взят в работу " ~ value) проверяются в test_dynamic_labels_are_translated
    messages = {m for m in source_messages() if not m.endswith(" ")}

    missing = sorted(m for m in messages if m not in en or m not in uz)

    assert not missing, missing


def test_every_script_string_is_translated_and_sent_to_browser():

    en = load("en")
    sent = set(i18n.SCRIPT_MESSAGES)

    for message in script_messages():
        assert message in en, message
        assert message in sent, message


def test_dynamic_labels_are_translated():

    en = load("en")

    values = list(protocols.SOURCE_LANGS.values()) + list(protocols.TARGET_LANGS.values())
    values += ["Взят в работу " + v for v in protocols.WORK_TAKEN]
    values += [v.capitalize() for v in protocols.ARCHIVE]
    values += [v.rstrip(":") for v in protocols.LABELS["ru"].values() if len(v) < 60]

    for value in values:
        if CYRILLIC.search(value) and "{" not in value and value != "ПАТОГИСТОЛОГИЧЕСКОЕ ИССЛЕДОВАНИЕ":
            if value in ("Микроскопическая картина", "Микроскопическое описание", "Патогистологическое заключение", "Макроскопическое описание", "Маркировка", "МКБ-О код", "Муж", "Жен", " г."):
                continue
            assert value in en, value


# ---------- Переключатель ----------

def test_default_language_is_russian(client):

    page = client.get("/login").get_data(as_text=True)

    assert '<html lang="ru">' in page
    assert "Войти" in page


def test_browser_language_is_used_until_chosen(client):

    page = client.get("/login", headers={"Accept-Language": "en-US,en;q=0.9"}).get_data(as_text=True)

    assert '<html lang="en">' in page
    assert "Sign in" in page


def test_switch_sets_cookie_and_returns_to_page(client):

    response = client.get("/lang/uz?next=/register")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/register")

    cookie = response.headers.get("Set-Cookie", "")
    assert "paithology_lang=uz" in cookie
    assert "HttpOnly" in cookie

    page = client.get("/register").get_data(as_text=True)

    assert '<html lang="uz">' in page
    assert "Roʻyxatdan oʻtish" in page


@pytest.mark.parametrize("target", ["//evil.example", "https://evil.example/", "/\\evil.example", "", "javascript:alert(1)"])
def test_switch_never_redirects_outside(client, target):

    response = client.get("/lang/en", query_string={"next": target})

    location = response.headers["Location"]

    assert location in ("/", "http://localhost/")


def test_unknown_language_is_404(client):

    assert client.get("/lang/de").status_code == 404


def test_switch_is_on_every_page(doctor):

    for url in ("/", "/cases", "/protocols/new", "/protocols/new/small", "/protocols/archive", "/settings", "/consultants", "/consultant-register"):
        page = doctor.get(url).get_data(as_text=True)
        assert 'class="lang-switch"' in page, url
        for code in ("ru", "en", "uz"):
            assert f"/lang/{code}?next=" in page, (url, code)


def test_public_pages_have_switch(client):

    for url in ("/login", "/register"):
        assert 'class="lang-switch"' in client.get(url).get_data(as_text=True), url


def test_english_pages_render(doctor):

    doctor.get("/lang/en?next=/")

    home = doctor.get("/").get_data(as_text=True)
    assert "Start analysis" in home and "Take photo" in home

    form = doctor.get("/protocols/new/small").get_data(as_text=True)
    assert "Gross description" in form and "Input language: Russian" in form

    # значения документа не переводятся: в документ уходит русский текст
    assert 'value="Мужской"' in form
    assert '<option value="полностью">Submitted entirely</option>' in form


def test_script_catalog_on_page(doctor):

    doctor.get("/lang/uz?next=/")

    page = doctor.get("/protocols/new/small").get_data(as_text=True)

    match = re.search(r"window\.PAI_I18N = (\{.*?\});", page)

    assert match
    catalog = json.loads(match.group(1))
    assert catalog["Сохраняю…"] == "Saqlanmoqda…"
    assert len(catalog) == len(i18n.SCRIPT_MESSAGES)


# ---------- Сообщения сервера ----------

def test_login_error_is_translated(client, app):

    create_user(app, "someone", "right-pass")

    client.get("/lang/en?next=/login")
    token = get_csrf(client, "/login")

    response = client.post("/login", data={"username": "someone", "password": "wrong", "csrf_token": token})

    assert "Wrong username or password" in response.get_data(as_text=True)


def test_protocol_error_is_translated(doctor):

    doctor.get("/lang/uz?next=/")
    token = get_csrf(doctor, "/protocols/new")

    data = {"kind": "small", "format": "docx", "csrf_token": token}
    data.update({f"c-{i}-diagnosis": "Тест" for i in range(protocols.MAX_CONCLUSIONS + 1)})

    response = doctor.post("/protocols/download", data=data)

    assert response.status_code == 400
    assert response.get_json()["error"] == f"{protocols.MAX_CONCLUSIONS} tadan ortiq xulosa boʻlmasligi kerak"


def test_protocol_error_text_stays_russian_for_logs():

    error = protocols.ProtocolError("Не больше {count} заключений", count=5)

    assert str(error) == "Не больше 5 заключений"


def test_result_page_switch_returns_to_saved_case(doctor):

    import io

    token = get_csrf(doctor, "/")

    response = doctor.post(
        "/",
        data={
            "csrf_token": token,
            "patient_name": "Тестов И",
            "organ": "Желудок",
            "images": (io.BytesIO(b"\xff\xd8\xff\xe0fake"), "photo_1.jpg"),
        },
        content_type="multipart/form-data",
    )

    page = response.get_data(as_text=True)

    # страница результата открыта POST-запросом: после смены языка
    # возвращаемся в сохранённое исследование, а не на пустую форму
    match = re.search(r'/lang/en\?next=([^"&]+)', page)

    assert match
    assert re.fullmatch(r"(/|%2F)case(/|%2F)\d+", match.group(1))


def test_no_free_attempts_page_is_html_and_translated(doctor, app):

    from database import db, User

    with app.app_context():
        user = User.query.filter_by(username="doctor").first()
        user.free_attempts = 0
        user.is_paid = False
        db.session.commit()

    doctor.get("/lang/en?next=/")
    token = get_csrf(doctor, "/")

    page = doctor.post("/", data={"csrf_token": token}).get_data(as_text=True)

    assert page == "<h1>You have used all free attempts</h1>"
