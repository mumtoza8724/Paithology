"""
Тесты архива патогистологических протоколов: сохранение, история
изменений, фото, доступ только владельца, удаление.

Только вымышленные данные и синтетические изображения.
"""

import io
import json
import os

import pytest
from docx import Document
from PIL import Image

from database import db, Protocol, ProtocolRevision

from conftest import create_user, get_csrf, login
from test_protocols import download, protocol_form, synthetic_image


@pytest.fixture
def two_doctors(app, make_client):

    create_user(app, "owner", "owner-pass")
    create_user(app, "other", "other-pass")

    owner, other = make_client(), make_client()

    login(owner, "owner", "owner-pass")
    login(other, "other", "other-pass")

    return owner, other


def save(client, data):

    token = get_csrf(client, "/protocols/new")

    return client.post(
        "/protocols/save",
        data={**data, "csrf_token": token},
        content_type="multipart/form-data"
    )


def stored(app, protocol_id):

    with app.app_context():
        protocol = db.session.get(Protocol, protocol_id)
        return json.loads(protocol.data) if protocol else None


def revisions(app, protocol_id):

    with app.app_context():
        return [
            (r.action, json.loads(r.changes))
            for r in ProtocolRevision.query.filter_by(protocol_id=protocol_id).order_by(ProtocolRevision.id)
        ]


def protocol_folder(app, protocol_id):

    return os.path.join(app.config["PROTOCOL_FOLDER"], stored(app, protocol_id)["storage"])


# ---------- Сохранение ----------

def test_download_saves_protocol_to_archive(app, two_doctors):

    owner, _ = two_doctors

    response = download(owner, "docx", protocol_form("small"))

    assert response.status_code == 200

    protocol_id = int(response.headers["X-Protocol-Id"])
    data = stored(app, protocol_id)

    assert data["fields"]["patient_name"] == "Тестова Мария"
    assert data["conclusions"][0]["diagnosis"] == "Протоковый инвазивный канцер G-2"
    assert revisions(app, protocol_id) == [("Создан", [])]

    page = owner.get("/protocols/archive").get_data(as_text=True)

    assert "Тестова Мария" in page
    assert "1234 р (1-7)/26" in page


def test_save_button_creates_protocol(app, two_doctors):

    owner, _ = two_doctors

    response = save(owner, protocol_form("large"))

    assert response.status_code == 200
    assert response.json["changed"] is True
    assert response.json["edit_url"] == f"/protocols/{response.json['id']}/edit"


def test_repeated_download_does_not_duplicate_or_add_history(app, two_doctors):

    owner, _ = two_doctors

    first = download(owner, "docx", protocol_form("small"))
    protocol_id = first.headers["X-Protocol-Id"]

    again = download(owner, "pdf", protocol_form("small", protocol_id=protocol_id))

    assert again.headers["X-Protocol-Id"] == protocol_id

    with app.app_context():
        assert Protocol.query.count() == 1

    assert len(revisions(app, int(protocol_id))) == 1


def test_edit_records_history(app, two_doctors):

    owner, _ = two_doctors

    protocol_id = save(owner, protocol_form("small")).json["id"]

    response = save(owner, protocol_form(
        "small",
        protocol_id=str(protocol_id),
        patient_name="Тестова Мария Ивановна",
        **{"c-0-icdo": "8500/2"},
    ))

    assert response.json["changed"] is True

    history = revisions(app, protocol_id)

    assert history[0] == ("Создан", [])
    assert history[1][0] == "Изменён"
    assert set(history[1][1]) == {"Пациент", "Заключение 1"}

    page = owner.get(f"/protocols/{protocol_id}").get_data(as_text=True)

    assert "История изменений" in page
    assert "Изменён" in page and "Заключение 1" in page
    assert "Тестова Мария Ивановна" in page


def test_unchanged_save_reports_no_changes(app, two_doctors):

    owner, _ = two_doctors

    protocol_id = save(owner, protocol_form("small")).json["id"]

    response = save(owner, protocol_form("small", protocol_id=str(protocol_id)))

    assert response.json["changed"] is False
    assert len(revisions(app, protocol_id)) == 1


def test_edit_page_prefills_form(app, two_doctors):

    owner, _ = two_doctors

    protocol_id = save(owner, protocol_form("large", macro_notes="наброски")).json["id"]

    page = owner.get(f"/protocols/{protocol_id}/edit").get_data(as_text=True)

    assert f'id="protocol-id" value="{protocol_id}"' in page

    # Данные для заполнения формы передаются в JSON (кириллица там экранирована)
    raw = page.split('<script type="application/json" id="protocol-initial">')[1].split("</script>")[0]
    initial = json.loads(raw)

    assert initial["id"] == protocol_id
    assert initial["fields"]["patient_name"] == "Тестова Мария"
    assert initial["conclusions"][0]["diagnosis"] == "Протоковый инвазивный канцер G-2"
    assert initial["macro_inputs"]["macro_notes"] == "наброски"


# ---------- Фото ----------

def test_photos_are_kept_replaced_and_removed(app, two_doctors):

    owner, _ = two_doctors

    created = save(owner, protocol_form(
        "large",
        macro_photo=(io.BytesIO(synthetic_image(color=(10, 200, 10))), "m.jpg"),
        **{"c-0-photo": (io.BytesIO(synthetic_image()), "c.jpg")},
    ))

    protocol_id = created.json["id"]
    data = stored(app, protocol_id)

    macro, micro = data["macro_photo"], data["conclusions"][0]["image"]

    assert macro and micro and macro != micro
    assert sorted(os.listdir(protocol_folder(app, protocol_id))) == sorted([macro, micro])

    # Повторное сохранение без новых файлов оставляет фото на месте
    save(owner, protocol_form(
        "large", protocol_id=str(protocol_id), macro_photo_existing=macro,
        **{"c-0-photo_existing": micro},
    ))

    assert stored(app, protocol_id)["macro_photo"] == macro
    assert stored(app, protocol_id)["conclusions"][0]["image"] == micro

    # Замена фото макро и удаление фото микро
    save(owner, protocol_form(
        "large", protocol_id=str(protocol_id),
        macro_photo=(io.BytesIO(synthetic_image(color=(200, 10, 10))), "new.jpg"),
    ))

    data = stored(app, protocol_id)

    assert data["macro_photo"] not in (None, macro)
    assert data["conclusions"][0]["image"] is None
    assert {"Фото макропрепарата", "Заключение 1"} <= set(revisions(app, protocol_id)[-1][1])


def test_existing_photo_name_from_other_protocol_is_ignored(app, two_doctors):

    owner, other = two_doctors

    foreign = save(other, protocol_form(
        "large", macro_photo=(io.BytesIO(synthetic_image()), "m.jpg")
    )).json["id"]

    foreign_name = stored(app, foreign)["macro_photo"]

    mine = save(owner, protocol_form("large", macro_photo_existing=foreign_name)).json["id"]

    assert stored(app, mine)["macro_photo"] is None


def test_archive_document_contains_saved_photos(app, two_doctors):

    owner, _ = two_doctors

    protocol_id = save(owner, protocol_form(
        "large",
        macro_photo=(io.BytesIO(synthetic_image()), "m.jpg"),
        **{"c-0-photo": (io.BytesIO(synthetic_image()), "c.jpg")},
    )).json["id"]

    response = owner.get(f"/protocols/{protocol_id}/download/docx")

    assert response.status_code == 200

    document = Document(io.BytesIO(response.data))

    assert len(document.inline_shapes) == 2

    pdf = owner.get(f"/protocols/{protocol_id}/download/pdf")

    assert pdf.data.startswith(b"%PDF")


def test_image_route(app, two_doctors, client):

    owner, other = two_doctors

    protocol_id = save(owner, protocol_form(
        "large", macro_photo=(io.BytesIO(synthetic_image()), "m.jpg")
    )).json["id"]

    name = stored(app, protocol_id)["macro_photo"]
    url = f"/protocols/{protocol_id}/image/{name}"

    response = owner.get(url)

    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.data)).format == "JPEG"
    assert "no-store" in response.headers["Cache-Control"]

    assert other.get(url).status_code == 404
    assert client.get(url).status_code == 302

    for bad in ("../../app.py", "x.jpg", name.replace(".jpg", ".exe")):
        assert owner.get(f"/protocols/{protocol_id}/image/{bad}").status_code == 404


def test_header_snapshot_kept_when_device_has_no_settings(app, two_doctors):

    owner, _ = two_doctors

    protocol_id = save(owner, protocol_form(
        "small", header_logo=(io.BytesIO(synthetic_image("PNG")), "logo.png")
    )).json["id"]

    header = stored(app, protocol_id)["header"]

    save(owner, protocol_form(
        "small", protocol_id=str(protocol_id),
        header_org="", header_form="", header_address="",
        patient_name="Другое Имя",
    ))

    assert stored(app, protocol_id)["header"] == header


def test_same_logo_is_not_a_change(app, two_doctors):

    owner, _ = two_doctors

    logo = synthetic_image("PNG")

    protocol_id = save(owner, protocol_form(
        "small", header_logo=(io.BytesIO(logo), "logo.png")
    )).json["id"]

    response = save(owner, protocol_form(
        "small", protocol_id=str(protocol_id), header_logo=(io.BytesIO(logo), "logo.png")
    ))

    assert response.json["changed"] is False
    assert len(os.listdir(protocol_folder(app, protocol_id))) == 1


# ---------- Доступ ----------

def test_other_user_cannot_access_protocol(app, two_doctors):

    owner, other = two_doctors

    protocol_id = save(owner, protocol_form("small")).json["id"]

    for url in (f"/protocols/{protocol_id}", f"/protocols/{protocol_id}/edit",
                f"/protocols/{protocol_id}/download/docx", f"/protocols/{protocol_id}/download/pdf"):
        assert other.get(url).status_code == 404, url

    token = get_csrf(other, "/protocols/new")

    assert other.post(f"/protocols/{protocol_id}/delete", data={"csrf_token": token}).status_code == 404

    assert "Тестова Мария" not in other.get("/protocols/archive").get_data(as_text=True)

    with app.app_context():
        assert db.session.get(Protocol, protocol_id) is not None


def test_other_user_cannot_overwrite_protocol(app, two_doctors):

    owner, other = two_doctors

    protocol_id = save(owner, protocol_form("small")).json["id"]
    before = stored(app, protocol_id)

    response = save(other, protocol_form("small", protocol_id=str(protocol_id), patient_name="Взлом"))

    assert response.status_code == 404
    assert download(other, "docx", protocol_form("small", protocol_id=str(protocol_id))).status_code == 404
    assert stored(app, protocol_id) == before


def test_anonymous_redirected(app, two_doctors, client):

    owner, _ = two_doctors

    protocol_id = save(owner, protocol_form("small")).json["id"]

    for url in (f"/protocols/{protocol_id}", f"/protocols/{protocol_id}/edit",
                f"/protocols/{protocol_id}/download/docx"):
        assert client.get(url).status_code == 302


def test_delete_requires_post_and_csrf(app, two_doctors):

    owner, _ = two_doctors

    protocol_id = save(owner, protocol_form("small")).json["id"]

    assert owner.get(f"/protocols/{protocol_id}/delete").status_code == 405
    assert owner.post(f"/protocols/{protocol_id}/delete").status_code == 400

    with app.app_context():
        assert db.session.get(Protocol, protocol_id) is not None


def test_delete_removes_protocol_history_and_files(app, two_doctors):

    owner, _ = two_doctors

    protocol_id = save(owner, protocol_form(
        "large", macro_photo=(io.BytesIO(synthetic_image()), "m.jpg")
    )).json["id"]

    folder = protocol_folder(app, protocol_id)

    assert os.path.isdir(folder)

    token = get_csrf(owner, f"/protocols/{protocol_id}")

    response = owner.post(f"/protocols/{protocol_id}/delete", data={"csrf_token": token})

    assert response.status_code == 302

    with app.app_context():
        assert db.session.get(Protocol, protocol_id) is None
        assert ProtocolRevision.query.filter_by(protocol_id=protocol_id).count() == 0

    assert not os.path.exists(folder)
    assert owner.get(f"/protocols/{protocol_id}").status_code == 404


# ---------- Поиск ----------

def test_archive_search(app, two_doctors):

    owner, _ = two_doctors

    save(owner, protocol_form("small", research_number="111 Д/26", patient_name="Первая Пациентка"))
    save(owner, protocol_form(
        "large", research_number="222 р/26", patient_name="Второй Пациент",
        **{"c-0-diagnosis": "Аденокарцинома желудка"},
    ))

    def rows(**query):
        return owner.get("/protocols/archive", query_string=query).get_data(as_text=True)

    assert "Первая" in rows() and "Второй" in rows()
    assert "Второй" not in rows(research_number="111")
    assert "Первая" not in rows(patient_name="Второй")
    assert "Первая" not in rows(conclusion="Аденокарцинома")
    assert "Ничего не найдено" in rows(patient_name="Нет такого")


def test_reused_protocol_number_gets_clean_folder(app, two_doctors):

    """SQLite может повторно выдать номер удалённого протокола: фото не смешиваются."""

    owner, _ = two_doctors

    first = save(owner, protocol_form("large", macro_photo=(io.BytesIO(synthetic_image()), "m.jpg"))).json["id"]
    first_folder = protocol_folder(app, first)

    # Имитация сброса базы: запись удалена, а старая папка осталась на диске
    with app.app_context():
        ProtocolRevision.query.delete()
        Protocol.query.delete()
        db.session.commit()

    second = save(owner, protocol_form("large", macro_photo=(io.BytesIO(synthetic_image()), "m.jpg"))).json["id"]

    assert second == first
    assert protocol_folder(app, second) != first_folder
    assert len(os.listdir(protocol_folder(app, second))) == 1
