"""
Доработки протоколов: календарь, клинический диагноз, несколько
макроописаний, пересмотр готового среза, колонтитулы, консультант
заглавными, имена файлов, главная страница («Оформить протокол», отчёт в
формате протокола) и расширенный справочник МКБ-О.

Только вымышленные данные и синтетические изображения.
"""

import io
import json
import os
from urllib.parse import quote

import pytest
from docx import Document

import protocols
from database import db, Case, CaseDetails, Protocol, ProtocolRevision

from conftest import create_user, get_csrf, login
from test_protocols import download, protocol_form, synthetic_image


@pytest.fixture
def doctor(app, client):

    create_user(app, "doctor", "doctor-pass")
    login(client, "doctor", "doctor-pass")

    return client


def post_json(client, url, payload):

    token = get_csrf(client, "/protocols/new")

    return client.post(url, json=payload, headers={"X-CSRF-Token": token})


def footers(document):

    return [
        "\n".join(p.text for p in section.footer.paragraphs)
        for section in document.sections
    ]


# ---------- 1. Календарь ----------

def test_birth_date_has_calendar_everywhere(doctor):

    for url in ("/protocols/new/small", "/protocols/new/large", "/"):
        page = doctor.get(url).get_data(as_text=True)
        assert 'name="birth_date"' in page, url
        assert "data-calendar-open" in page and "data-calendar-proxy" in page, url


# ---------- 2. Клинический диагноз ----------

def test_clinical_generation(doctor):

    data = post_json(doctor, "/protocols/generate/clinical", {
        "text": "рак   левой молочной железы  т2и2мо", "source_lang": "ru", "doc_lang": "ru",
    }).json

    assert data["text"] == "Рак левой молочной железы T2N2M0."
    assert "грамматик" in data["notice"]


def test_clinical_translation_notice(doctor):

    data = post_json(doctor, "/protocols/generate/clinical", {
        "text": "кўкрак бези саратони", "source_lang": "uz", "doc_lang": "ru",
    }).json

    assert "Узбекский (кириллица)" in data["notice"]


def test_clinical_requires_login_and_csrf(app, client, doctor):

    anonymous = app.test_client()
    token = get_csrf(anonymous, "/login")

    assert anonymous.post("/protocols/generate/clinical", json={}, headers={"X-CSRF-Token": token}).status_code == 302
    assert doctor.post("/protocols/generate/clinical", json={"text": "x"}).status_code == 400


def test_clinical_field_on_main_and_protocol_pages(doctor):

    for url in ("/", "/protocols/new/small"):
        page = doctor.get(url).get_data(as_text=True)
        assert "data-clinical-generate" in page and 'name="clinical_lang"' in page, url


# ---------- 3. Несколько макроописаний ----------

def test_macro_block_small_with_notes_and_partial_fields(doctor):

    data = post_json(doctor, "/protocols/generate/macro", {
        "kind": "small", "doc_lang": "ru", "start": 1, "intro": True,
        "block": {"size": "0,5х0,5 см", "notes": "3 фрагмента", "lang": "ru", "work": "частично", "archive": "имеется архив"},
    }).json

    assert data["text"] == (
        "Биопсийный материал, фиксированный формалином: ткань размерами 0,5х0,5 см. "
        "Взят в работу частично, имеется архив. 3 фрагмента."
    )
    assert "…" not in data["text"]


def test_macro_block_large_continues_numbering(doctor):

    data = post_json(doctor, "/protocols/generate/macro", {
        "kind": "large", "doc_lang": "ru", "start": 3, "intro": False,
        "block": {"notes": "лимфоузлы от 0,5 до 2,0 см\nжировая клетчатка", "lang": "uz"},
    }).json

    assert data["text"] == "3. Лимфоузлы от 0,5 до 2,0 см.\n4. Жировая клетчатка."
    assert data["count"] == 2
    assert "Узбекский (кириллица)" in data["notice"]


def test_marking_generation(doctor):

    data = post_json(doctor, "/protocols/generate/marking", {"notes": "опухоль 1-2, лимфоузлы 5 6 7"}).json

    assert data["marking"] == "опухоль – 1-2, лимфоузлы – 5-6-7."


def test_several_macro_blocks_in_document(doctor):

    form = protocol_form(
        "large",
        **{
            "mb-0-text": "Операционный материал, фиксированный формалином:\n1. Фрагмент молочной железы.",
            "mb-0-notes": "фрагмент", "mb-0-lang": "ru",
            "mb-1-text": "2. Лимфоузлы.", "mb-1-notes": "лимфоузлы", "mb-1-lang": "ru",
        },
    )
    form.pop("macro_text")

    text = "\n".join(p.text for p in Document(io.BytesIO(download(doctor, "docx", form).data)).paragraphs)

    assert "1. Фрагмент молочной железы." in text
    assert "2. Лимфоузлы." in text
    assert text.index("1. Фрагмент") < text.index("2. Лимфоузлы")


def test_too_many_macro_blocks(doctor):

    extra = {f"mb-{i}-text": f"Блок {i}" for i in range(protocols.MAX_MACRO_BLOCKS + 1)}

    assert download(doctor, "docx", protocol_form(**extra)).status_code == 400


def test_main_page_has_macro_blocks_instead_of_old_fields(doctor):

    page = doctor.get("/").get_data(as_text=True)

    assert "data-macro-blocks" in page and "data-add-macro" in page
    assert 'name="material_size"' not in page
    assert 'name="doc_lang"' in page


# ---------- 4, 8. Колонтитул: консультант заглавными и примечание ----------

def test_consultant_and_note_in_second_page_footer(doctor):

    response = download(doctor, "docx", protocol_form("small"))

    document = Document(io.BytesIO(response.data))
    first, second = footers(document)[:2]
    body = "\n".join(p.text for p in document.paragraphs)

    assert "КОНСУЛЬТАНТ, Д.М.Н.: ____________ КОНСУЛЬТАНТОВ К.К." in second
    assert "Примечание: окончательная постановка диагноза" in second
    assert "Примечание" not in first and "КОНСУЛЬТАНТ" not in first
    assert "Примечание" not in body and "Консультант" not in body


def test_pdf_has_two_pages_with_footer(doctor):

    response = download(doctor, "pdf", protocol_form("large"))

    assert response.data.startswith(b"%PDF")
    assert response.data.count(b"/Type /Page\n") == 2


# ---------- 7. Пересмотр готового среза ----------

def test_review_mode_document(doctor):

    form = protocol_form("small", review="1", research_number="6438-40/26", operation_type="пересмотр")

    text = "\n".join(p.text for p in Document(io.BytesIO(download(doctor, "docx", form).data)).paragraphs)

    assert "Пересмотр готового среза № 6438-40/26" in text
    assert "№6438-40/26" not in text
    assert "Макроскопическое описание" not in text
    assert "Маркировка:" not in text


def test_review_mode_english(doctor):

    form = protocol_form("small", review="1", doc_lang="en", research_number="6438-40/26")

    text = "\n".join(p.text for p in Document(io.BytesIO(download(doctor, "docx", form).data)).paragraphs)

    assert "Review of submitted slides No. 6438-40/26" in text


def test_review_flag_saved_and_in_history(app, doctor):

    token = get_csrf(doctor, "/protocols/new")

    first = doctor.post("/protocols/save", data={**protocol_form("small"), "csrf_token": token},
                        content_type="multipart/form-data").json

    doctor.post("/protocols/save", data={**protocol_form("small", protocol_id=str(first["id"]), review="1"),
                                         "csrf_token": token}, content_type="multipart/form-data")

    with app.app_context():
        data = json.loads(db.session.get(Protocol, first["id"]).data)
        changes = json.loads(ProtocolRevision.query.filter_by(protocol_id=first["id"]).order_by(ProtocolRevision.id.desc()).first().changes)

    assert data["review"] is True
    assert data["macro_text"] == ""
    assert "Пересмотр готового среза" in changes


def test_old_protocol_without_review_is_not_reported_as_changed(app, doctor):

    token = get_csrf(doctor, "/protocols/new")

    first = doctor.post("/protocols/save", data={**protocol_form("small"), "csrf_token": token},
                        content_type="multipart/form-data").json

    # Протокол, сохранённый до появления пересмотра: поля review нет
    with app.app_context():
        protocol = db.session.get(Protocol, first["id"])
        data = json.loads(protocol.data)
        data.pop("review")
        protocol.data = json.dumps(data, ensure_ascii=False)
        db.session.commit()

    again = doctor.post("/protocols/save", data={**protocol_form("small", protocol_id=str(first["id"])),
                                                 "csrf_token": token}, content_type="multipart/form-data").json

    assert again["changed"] is False


# ---------- 5. Имя файла ----------

def test_download_file_name_is_patient_and_number(doctor):

    response = download(doctor, "docx", protocol_form("small", patient_name="Тестова Мария", research_number="6438-40/26"))

    assert quote("Тестова Мария 6438-40-26.docx") in response.headers["Content-Disposition"]


def test_settings_page_has_defaults_and_folder(doctor):

    page = doctor.get("/settings").get_data(as_text=True)

    for value in ('id="defaults-form"', 'name="lab_worker"', 'name="stain_method"',
                  'name="consultant_title"', 'name="consultant_name"', 'id="f-choose"'):
        assert value in page, value


# ---------- 9. Главная страница и протоколы ----------

def analyze(client, **extra):

    token = get_csrf(client, "/")

    data = {
        "csrf_token": token,
        "research_number": "7001 Д/26",
        "patient_name": "Тестов Иван",
        "gender": "Мужской",
        "birth_date": "1969 г",
        "organ": "Желудок",
        "clinical": "Новообразование желудка",
        "lab_worker": "Лаборантова Л.",
        "stain_method": "Гематоксилин-эозин",
        "doc_lang": "ru",
        "clinical_lang": "uz",
        "mb-0-size": "1,0х0,3 см",
        "mb-0-color": "белесоватого",
        "mb-0-consistency": "мягкой",
        "mb-0-text": "Биопсийный материал: ткань 1,0х0,3 см.",
        "mb-1-text": "Второй фрагмент 0,5 см.",
        "images": [(io.BytesIO(synthetic_image()), "slide.jpg")],
    }
    data.update(extra)

    return client.post("/", data=data, content_type="multipart/form-data")


def latest_case(app):

    with app.app_context():
        case = Case.query.order_by(Case.id.desc()).first()
        details = CaseDetails.query.filter_by(case_id=case.id).first()
        return case.id, case.material_size, case.material_color, json.loads(details.data)


def test_main_page_saves_macro_blocks(app, doctor):

    response = analyze(doctor)

    assert response.status_code == 200

    case_id, size, color, details = latest_case(app)

    assert size == "1,0х0,3 см" and color == "белесоватого"
    assert details["clinical_lang"] == "uz"
    assert [b["text"] for b in details["macro_blocks"]] == [
        "Биопсийный материал: ткань 1,0х0,3 см.", "Второй фрагмент 0,5 см.",
    ]

    page = response.get_data(as_text=True)

    assert f"/report/{case_id}" in page
    assert f"/cases/{case_id}/protocol" in page
    assert 'href="/report"' not in page


def test_case_report_in_protocol_format(app, doctor):

    analyze(doctor)
    case_id = latest_case(app)[0]

    token = get_csrf(doctor, f"/case/{case_id}")

    response = doctor.post(f"/report/{case_id}", data={
        "csrf_token": token, "format": "docx",
        "header_org": "ООО «ТЕСТОВАЯ ЛАБОРАТОРИЯ»",
        "consultant_title": "Консультант", "consultant_name": "Консультантов К.К.",
        "header_logo": (io.BytesIO(synthetic_image("PNG")), "logo.png"),
    }, content_type="multipart/form-data")

    assert response.status_code == 200
    assert quote("Тестов Иван 7001 Д-26.docx") in response.headers["Content-Disposition"]

    document = Document(io.BytesIO(response.data))
    body = "\n".join(p.text for p in document.paragraphs)
    tables = "\n".join(c.text for t in document.tables for r in t.rows for c in r.cells)

    assert "ПАТОГИСТОЛОГИЧЕСКОЕ ИССЛЕДОВАНИЕ" in body
    assert "Биопсийный материал: ткань 1,0х0,3 см." in body and "Второй фрагмент 0,5 см." in body
    assert "Тестов Иван" in tables and "Желудок" in tables
    assert "КОНСУЛЬТАНТ: ____________ КОНСУЛЬТАНТОВ К.К." in footers(document)[1]
    assert "ООО «ТЕСТОВАЯ ЛАБОРАТОРИЯ»" in tables

    pdf = doctor.post(f"/report/{case_id}", data={"csrf_token": token, "format": "pdf"},
                      content_type="multipart/form-data")

    assert pdf.data.startswith(b"%PDF")


def test_case_report_get_still_works(app, doctor):

    analyze(doctor)
    case_id = latest_case(app)[0]

    response = doctor.get(f"/report/{case_id}")

    assert response.status_code == 200 and response.data[:2] == b"PK"


def test_case_to_protocol(app, doctor):

    analyze(doctor)
    case_id = latest_case(app)[0]

    token = get_csrf(doctor, f"/case/{case_id}")

    response = doctor.post(f"/cases/{case_id}/protocol", data={"csrf_token": token, "kind": "large"})

    assert response.status_code == 302
    assert "from_case=1" in response.location

    with app.app_context():
        protocol = Protocol.query.order_by(Protocol.id.desc()).first()
        data = json.loads(protocol.data)
        history = [r.action for r in ProtocolRevision.query.filter_by(protocol_id=protocol.id)]

    assert protocol.kind == "large"
    assert data["fields"]["patient_name"] == "Тестов Иван"
    assert data["fields"]["marking"] == "Желудок"
    assert data["fields"]["clinical"] == "Новообразование желудка"
    assert [b["text"] for b in data["macro_blocks"]][1] == "Второй фрагмент 0,5 см."
    # Заключение и описание из ИИ-анализа (в тестах — заглушка нейросети)
    assert data["conclusions"][0]["diagnosis"] == "Тестовое заключение"
    assert data["conclusions"][0]["micro"] == "Тестовое описание"
    assert data["conclusions"][0]["image"]
    assert data["macro_inputs"]["clinical_lang"] == "uz"
    assert history == ["Создан из ИИ-анализа"]

    edit = doctor.get(response.location).get_data(as_text=True)

    assert 'data-from-case="1"' in edit
    assert "Протокол создан из ИИ-анализа" in edit


def test_case_to_protocol_access(app, doctor, make_client):

    analyze(doctor)
    case_id = latest_case(app)[0]

    create_user(app, "other", "other-pass")
    other = make_client()
    login(other, "other", "other-pass")

    token = get_csrf(other, "/")

    assert other.post(f"/cases/{case_id}/protocol", data={"csrf_token": token, "kind": "small"}).status_code == 404
    assert other.post(f"/report/{case_id}", data={"csrf_token": token}).status_code == 404
    assert doctor.get(f"/cases/{case_id}/protocol").status_code == 405
    assert doctor.post(f"/cases/{case_id}/protocol", data={"kind": "small"}).status_code == 400

    token = get_csrf(doctor, "/")

    assert doctor.post(f"/cases/{case_id}/protocol", data={"csrf_token": token, "kind": "x"}).status_code == 400

    with app.app_context():
        assert Protocol.query.count() == 0


def test_old_case_without_details_converts(app, doctor):

    """Исследование, сохранённое до появления макроописаний на главной."""

    analyze(doctor)
    case_id = latest_case(app)[0]

    with app.app_context():
        CaseDetails.query.delete()
        db.session.commit()

    token = get_csrf(doctor, "/")

    assert doctor.post(f"/cases/{case_id}/protocol", data={"csrf_token": token, "kind": "small"}).status_code == 302

    with app.app_context():
        data = json.loads(Protocol.query.first().data)

    assert data["macro_text"].startswith("Биопсийный материал, фиксированный формалином: ткань размерами 1,0х0,3 см")


# ---------- 10. Расширенный справочник МКБ-О ----------

@pytest.mark.parametrize("diagnosis, code", [
    ("Протоковая карцинома in situ", "8500/2"),
    ("Дольковый рак молочной железы", "8520/3"),
    ("Перстневидноклеточный рак желудка", "8490/3"),
    ("Аденокарцинома слизистой желудка", "8140/3"),
    ("Серозная карцинома высокой степени злокачественности", "8461/3"),
    ("Светлоклеточный почечно-клеточный рак", "8310/3"),
    ("Папиллярный рак щитовидной железы", "8260/3"),
    ("Уротелиальный рак мочевого пузыря", "8120/3"),
    ("Плоскоклеточный неороговевающий рак", "8072/3"),
    ("Мелкоклеточный рак легкого", "8041/3"),
    ("Диффузная В-крупноклеточная лимфома", "9680/3"),
    ("Лимфома Ходжкина", "9650/3"),
    ("Меланома кожи", "8720/3"),
    ("Липосаркома", "8850/3"),
    ("ГИСО желудка", "8936/3"),
    ("Тубулярная аденома толстой кишки", "8211/0"),
    ("Плеоморфная аденома околоушной железы", "8940/0"),
    ("Менингиома", "9530/0"),
    ("CIN I шейки матки", "8077/0"),
    ("Базалиома кожи", "8090/3"),
    ("Аденомиоз матки", ""),
    ("Реактивная гиперплазия лимфатических узлов", ""),
    ("Изменения шейки матки", ""),
])
def test_extended_icdo(diagnosis, code):

    assert protocols.suggest_icdo(diagnosis) == code
