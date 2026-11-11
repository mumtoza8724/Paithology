"""
Тесты страниц и сборки патогистологических протоколов.

Только вымышленные данные и синтетические изображения. Протоколы не
сохраняются: тесты также проверяют, что после скачивания в базе и в папке
загрузок ничего не появилось.
"""

import io
import os
import sys

import pytest
from docx import Document
from PIL import Image

import protocols
from database import db, Case

from conftest import create_user, get_csrf, login


PATIENT = "Тестова Мария"


def synthetic_image(fmt="JPEG", color=(240, 190, 220), size=(300, 200), exif=None):

    image = Image.new("RGB", size, color)
    out = io.BytesIO()

    if exif is not None:
        image.save(out, fmt, exif=exif)
    else:
        image.save(out, fmt)

    return out.getvalue()


@pytest.fixture
def doctor(app, client):

    create_user(app, "doctor", "doctor-pass")
    login(client, "doctor", "doctor-pass")

    return client


@pytest.fixture
def micro_templates(monkeypatch):

    """Шаблоны микроописаний вместо настоящего model.py (TensorFlow в тестах не грузится)."""

    texts = {
        "Протоковый инвазивный рак": "Синтетическое микроописание протокового рака.",
        "Аденокарцинома желудка": "Синтетическое микроописание аденокарциномы.",
    }

    monkeypatch.setattr(
        sys.modules["model"],
        "generate_microdescription",
        lambda diagnosis, organ: texts.get(diagnosis, "требующие дополнительной оценки"),
        raising=False
    )


def post_json(client, url, payload):

    token = get_csrf(client, "/protocols/new")

    return client.post(url, json=payload, headers={"X-CSRF-Token": token})


def protocol_form(kind="small", **extra):

    data = {
        "kind": kind,
        "doc_lang": "ru",
        "research_number": "1234 р (1-7)/26",
        "admission_date": "2026-11-03",
        "medical_center": "Тестовая клиника",
        "patient_name": PATIENT,
        "gender": "Женский",
        "birth_date": "1970 г",
        "operation_type": "Радикальная резекция",
        "marking": "Молочная железа, лимфоузлы",
        "clinical": "Новообразование",
        "doctor": "Врачев В.В.",
        "slides": "7",
        "cassettes": "1",
        "stain_method": "Гематоксилин-эозин",
        "lab_worker": "Лаборантова Л.",
        "result_date": "2026-11-04",
        "macro_text": "Операционный материал, фиксированный формалином:\n1. Фрагмент ткани.\n2. Край резекции.",
        "marking_text": "опухоль – 1-2, линия резекции – 3-4.",
        "c-0-diagnosis": "Протоковый инвазивный канцер G-2",
        "c-0-icdo": "8500/3",
        "c-0-micro": "Синтетическое микроописание.",
        "c-3-diagnosis": "Линии резекции интактные",
        "c-3-icdo": "",
        "c-3-micro": "",
        "consultant_title": "Консультант, д.м.н.",
        "consultant_name": "Консультантов К.К.",
        "header_org": "ООО «ТЕСТОВАЯ ЛАБОРАТОРИЯ»",
        "header_form": "Форма № 014",
        "header_address": "Адрес: г. Тестовый, ул. Примерная, 1",
    }

    data.update(extra)

    return data


def download(client, fmt, data):

    token = get_csrf(client, "/protocols/new")

    return client.post(
        "/protocols/download",
        data={**data, "format": fmt, "csrf_token": token},
        content_type="multipart/form-data"
    )


def docx_text(content):

    document = Document(io.BytesIO(content))

    parts = [p.text for p in document.paragraphs]

    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)

    return "\n".join(parts), document


# ---------- Доступ ----------

@pytest.mark.parametrize("url", [
    "/protocols/new", "/protocols/new/small", "/protocols/new/large",
    "/protocols/archive", "/settings",
])
def test_pages_require_login(client, url):

    response = client.get(url)

    assert response.status_code == 302
    assert response.location.endswith("/login")


@pytest.mark.parametrize("url", [
    "/protocols/generate/macro", "/protocols/generate/micro", "/protocols/download",
])
def test_actions_require_login(client, url):

    token = get_csrf(client, "/login")

    response = client.post(url, data={"csrf_token": token})

    assert response.status_code == 302


@pytest.mark.parametrize("url", [
    "/protocols/generate/macro", "/protocols/generate/micro", "/protocols/download",
])
def test_actions_require_csrf(doctor, url):

    assert doctor.post(url, json={"kind": "small"}).status_code == 400


def test_pages_render(doctor):

    for url in ("/protocols/new", "/protocols/new/small", "/protocols/new/large",
                "/protocols/archive", "/settings"):
        assert doctor.get(url).status_code == 200, url


def test_unknown_kind_is_404(doctor):

    assert doctor.get("/protocols/new/medium").status_code == 404


def test_sidebar_has_protocol_links(doctor):

    page = doctor.get("/cases").get_data(as_text=True)

    assert "Создать протокол" in page
    assert "Архив протоколов" in page
    assert 'href="/settings"' in page


def test_forms_contain_lists_and_note(doctor):

    small = doctor.get("/protocols/new/small").get_data(as_text=True)
    large = doctor.get("/protocols/new/large").get_data(as_text=True)

    for value in ("Гематоксилин-эозин", "Ван Гизон", "белесоватого", "мягкой",
                  "без архива", "имеется архив", "Протоковый инвазивный рак",
                  "окончательная постановка диагноза"):
        assert value in small, value

    for value in ("Узбекский (кириллица)", "English", "macro_photo", "marking_text"):
        assert value in large, value

    assert "macro_photo" not in small


# ---------- Генерация текстов ----------

def test_generate_macro_small(doctor):

    response = post_json(doctor, "/protocols/generate/macro", {
        "kind": "small", "doc_lang": "ru", "size": "1,0х0,3 см",
        "color": "белесоватый", "consistency": "мягкая",
        "work": "частично", "archive": "имеется архив",
    })

    assert response.status_code == 200
    assert response.json["text"] == (
        "Биопсийный материал, фиксированный формалином: ткань размерами "
        "1,0х0,3 см, белесоватого цвета, мягкой консистенции. "
        "Взят в работу частично, имеется архив."
    )


def test_generate_macro_small_english(doctor):

    response = post_json(doctor, "/protocols/generate/macro", {
        "kind": "small", "doc_lang": "en", "size": "1.0x0.3 cm",
        "color": "белесоватого", "consistency": "мягкой",
        "work": "полностью", "archive": "без архива",
    })

    assert "whitish" in response.json["text"]
    assert "soft consistency" in response.json["text"]
    assert response.json["notice"] == ""


def test_generate_macro_large_and_marking(doctor):

    response = post_json(doctor, "/protocols/generate/macro", {
        "kind": "large", "doc_lang": "ru", "source_lang": "ru",
        "notes": "фрагмент ткани 7х5 см\n2) край резекции",
        "marking_notes": "опухоль 1-2, линия резекции 3 4, лимфоузлы 5 6 7",
    })

    data = response.json

    assert data["text"].splitlines() == [
        "Операционный материал, фиксированный формалином:",
        "1. Фрагмент ткани 7х5 см.",
        "2. Край резекции.",
    ]
    assert data["marking"] == "опухоль – 1-2, линия резекции – 3-4, лимфоузлы – 5-6-7."
    assert data["notice"] == ""


def test_translation_notice_when_languages_differ(doctor):

    response = post_json(doctor, "/protocols/generate/macro", {
        "kind": "large", "doc_lang": "ru", "source_lang": "uz",
        "notes": "тўқима", "marking_notes": "",
    })

    assert "Узбекский (кириллица)" in response.json["notice"]
    assert "подключается позже" in response.json["notice"]


def test_generate_micro_exact_and_fuzzy(doctor, micro_templates):

    exact = post_json(doctor, "/protocols/generate/micro", {
        "diagnosis": "Аденокарцинома желудка", "doc_lang": "ru",
    }).json

    assert exact == {
        "micro": "Синтетическое микроописание аденокарциномы.",
        "icdo": "8140/3",
        "notice": "",
    }

    fuzzy = post_json(doctor, "/protocols/generate/micro", {
        "diagnosis": "Протоковый инвазивный канцер G-2", "doc_lang": "ru",
    }).json

    assert fuzzy["micro"] == "Синтетическое микроописание протокового рака."
    assert fuzzy["icdo"] == "8500/3"
    assert "Протоковый инвазивный рак" in fuzzy["notice"]


def test_generate_micro_without_template(doctor, micro_templates):

    data = post_json(doctor, "/protocols/generate/micro", {
        "diagnosis": "Линии резекции интактные", "doc_lang": "ru",
    }).json

    assert data["micro"] == ""
    assert data["icdo"] == ""
    assert "вручную" in data["notice"]


def test_generate_micro_english_is_manual_for_now(doctor, micro_templates):

    data = post_json(doctor, "/protocols/generate/micro", {
        "diagnosis": "Аденокарцинома желудка", "doc_lang": "en",
    }).json

    assert data["micro"] == ""
    assert data["icdo"] == "8140/3"
    assert data["notice"]


def test_generate_macro_rejects_unknown_kind(doctor):

    assert post_json(doctor, "/protocols/generate/macro", {"kind": "x"}).status_code == 400


def test_generate_ignores_non_string_values(doctor):

    response = post_json(doctor, "/protocols/generate/macro", {
        "kind": "small", "size": ["x"], "color": {"a": 1}, "consistency": 5,
    })

    assert response.status_code == 200


# ---------- Скачивание ----------

def test_download_large_docx(app, doctor):

    data = protocol_form(
        "large",
        macro_photo=(io.BytesIO(synthetic_image(color=(235, 215, 190))), "macro.jpg"),
        **{"c-0-photo": (io.BytesIO(synthetic_image()), "micro.jpg")},
        header_logo=(io.BytesIO(synthetic_image("PNG", (255, 255, 255))), "logo.png"),
    )

    response = download(doctor, "docx", data)

    assert response.status_code == 200
    assert response.mimetype.endswith("wordprocessingml.document")
    assert "no-store" in response.headers["Cache-Control"]
    assert "protocol_1234" in response.headers["Content-Disposition"]

    text, document = docx_text(response.data)

    for expected in (
        "ПАТОГИСТОЛОГИЧЕСКОЕ ИССЛЕДОВАНИЕ", "№1234 р (1-7)/26", PATIENT,
        "03.11.2026 г.", "Жен", "7 стекол; 1 кассета", "Гематоксилин-эозин",
        "Макроскопическое описание:", "1. Фрагмент ткани.", "Маркировка:",
        "Микроскопическое описание:", "1) Синтетическое микроописание.",
        "Патогистологическое заключение:", "1. Протоковый инвазивный канцер G-2.",
        "МКБ-О код 8500/3.", "2. Линии резекции интактные.",
        "Консультант, д.м.н.: ____________ Консультантов К.К.",
        "окончательная постановка диагноза", "ООО «ТЕСТОВАЯ ЛАБОРАТОРИЯ»",
    ):
        assert expected in text, expected

    # Фото макро и микро в теле документа, логотип в колонтитуле
    assert len(document.inline_shapes) == 2
    header_xml = document.sections[0].header._element.xml
    assert "graphicData" in header_xml
    assert "Адрес: г. Тестовый" in header_xml


def test_download_small_docx_has_picture_table(doctor):

    data = protocol_form(
        "small",
        **{
            "c-0-diagnosis": "Аденокарцинома желудка",
            "c-0-micro": "Синтетическое описание.",
            "c-0-icdo": "8140/3",
            "c-0-photo": (io.BytesIO(synthetic_image()), "micro.jpg"),
            "c-3-diagnosis": "",
        }
    )

    response = download(doctor, "docx", data)

    text, document = docx_text(response.data)

    assert "Микроскопическая картина:" in text
    assert "Патогистологическое заключение: Аденокарцинома желудка. МКБ-О код 8140/3." in text
    assert "Маркировка:" not in text
    assert len(document.inline_shapes) == 1


def test_download_english_labels(doctor):

    response = download(doctor, "docx", protocol_form("small", doc_lang="en"))

    text, _ = docx_text(response.data)

    for expected in ("HISTOPATHOLOGICAL EXAMINATION", "Date of specimen receipt:",
                     "Female", "7 slides; 1 cassette", "Hematoxylin and eosin",
                     "Note: the final diagnosis", "ICD-O code 8500/3"):
        assert expected in text, expected


def test_download_pdf(doctor):

    data = protocol_form(
        "large",
        macro_photo=(io.BytesIO(synthetic_image()), "macro.jpg"),
        header_logo=(io.BytesIO(synthetic_image("PNG")), "logo.png"),
    )

    response = download(doctor, "pdf", data)

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")
    assert response.data.count(b"/Type /Page\n") == 2


def test_download_rejects_non_image(doctor):

    data = protocol_form("large", macro_photo=(io.BytesIO(b"not an image"), "x.jpg"))

    response = download(doctor, "docx", data)

    assert response.status_code == 400
    assert "изображением" in response.json["error"]


def test_download_rejects_unknown_format_and_kind(doctor):

    assert download(doctor, "exe", protocol_form()).status_code == 400
    assert download(doctor, "docx", protocol_form("medium")).status_code == 400


def test_download_limits_conclusions(doctor):

    extra = {f"c-{i}-diagnosis": f"Заключение {i}" for i in range(protocols.MAX_CONCLUSIONS + 1)}

    response = download(doctor, "docx", protocol_form(**extra))

    assert response.status_code == 400


def test_download_stores_nothing(app, doctor):

    uploads = os.path.join("static", "uploads")
    before = sorted(os.listdir(uploads)) if os.path.isdir(uploads) else []

    for fmt in ("docx", "pdf"):
        data = protocol_form("large", macro_photo=(io.BytesIO(synthetic_image()), "m.jpg"))
        assert download(doctor, fmt, data).status_code == 200

    after = sorted(os.listdir(uploads)) if os.path.isdir(uploads) else []

    assert before == after

    with app.app_context():
        assert db.session.query(Case).count() == 0


# ---------- Вспомогательные функции ----------

def test_images_lose_metadata():

    exif = Image.Exif()
    exif[0x010F] = "SyntheticCamera"

    processed = protocols.process_image(synthetic_image(exif=exif.tobytes()))

    assert Image.open(io.BytesIO(processed)).getexif().get(0x010F) is None


def test_image_pixel_limit(monkeypatch):

    monkeypatch.setattr(protocols, "MAX_IMAGE_PIXELS", 100)

    with pytest.raises(protocols.ProtocolError):
        protocols.process_image(synthetic_image())


@pytest.mark.parametrize("number, expected", [
    (1, "стекло"), (2, "стекла"), (5, "стекол"), (11, "стекол"), (21, "стекло"), (24, "стекла"),
])
def test_plural(number, expected):

    assert protocols.plural_ru(number, "стекло", "стекла", "стекол") == expected


@pytest.mark.parametrize("value, feminine, expected", [
    ("белесоватый", False, "белесоватого"), ("серого", False, "серого"),
    ("синий", False, "синего"), ("мягкий", False, "мягкого"),
    ("мягкая", True, "мягкой"), ("плотной", True, "плотной"),
])
def test_genitive(value, feminine, expected):

    assert protocols.to_genitive_adjective(value, feminine) == expected


@pytest.mark.parametrize("diagnosis, code", [
    ("Протоковый инвазивный канцер G-2", "8500/3"),
    ("Базалиома кожи", "8090/3"),
    ("Муцинозная аденокарцинома желудка", "8480/3"),
    ("CIN III шейки матки", "8077/2"),
    ("Хронический гастрит", ""),
    ("", ""),
])
def test_icdo_suggestions(diagnosis, code):

    assert protocols.suggest_icdo(diagnosis) == code


def test_file_name_is_safe():

    data = {"fields": {"research_number": "../12 р/26"}}

    name = protocols.file_name(data, "pdf")

    assert "/" not in name and ".." not in name
    assert name.endswith(".pdf")

    # Латиница: кириллица в имени загрузки поддерживается не всеми браузерами
    assert protocols.file_name({"fields": {"research_number": "7883 Д/26"}}, "docx") == "protocol_7883_D_26.docx"
    assert protocols.file_name({"fields": {"research_number": ""}}, "docx") == "protocol_paithology.docx"
