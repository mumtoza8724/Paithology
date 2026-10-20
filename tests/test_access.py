"""
Проверки доступа: исследования, отчёты, админка, документы консультантов,
пароли и CSRF.
"""

import io
import os

import pytest

import app as app_module
from database import db, Consultant, User

from conftest import (
    case_exists,
    create_case,
    create_consultant,
    create_user,
    get_csrf,
    login
)


@pytest.fixture
def two_users(app, make_client):

    owner_id = create_user(app, "owner", "owner-pass")
    other_id = create_user(app, "other", "other-pass")

    case_id = create_case(app, owner_id, patient_name="Пациент Владельца")

    owner = make_client()
    other = make_client()

    assert login(owner, "owner", "owner-pass").status_code == 302
    assert login(other, "other", "other-pass").status_code == 302

    return {
        "case_id": case_id,
        "owner": owner,
        "other": other,
        "owner_id": owner_id
    }


# ---------- Аноним ----------

def test_anonymous_redirected_from_archive(client):

    response = client.get("/cases")

    assert response.status_code == 302
    assert response.location.endswith("/login")


def test_anonymous_cannot_view_case(app, client):

    case_id = create_case(app, create_user(app, "u", "p"))

    response = client.get(f"/case/{case_id}")

    assert response.status_code == 302
    assert response.location.endswith("/login")


def test_anonymous_cannot_download_report(app, client):

    case_id = create_case(app, create_user(app, "u", "p"))

    response = client.get(f"/report/{case_id}")

    assert response.status_code == 302
    assert response.location.endswith("/login")
    assert b"PK" not in response.data[:2]


def test_anonymous_cannot_delete_case(app, client):

    case_id = create_case(app, create_user(app, "u", "p"))

    token = get_csrf(client, "/login")

    response = client.post(
        f"/delete_case/{case_id}",
        data={"csrf_token": token}
    )

    assert response.status_code == 302
    assert response.location.endswith("/login")
    assert case_exists(app, case_id)


def test_delete_by_get_is_not_allowed(app, two_users):

    response = two_users["owner"].get(f"/delete_case/{two_users['case_id']}")

    assert response.status_code == 405
    assert case_exists(app, two_users["case_id"])


# ---------- Чужой пользователь ----------

def test_other_user_cannot_view_case(two_users):

    response = two_users["other"].get(f"/case/{two_users['case_id']}")

    assert response.status_code == 404


def test_other_user_cannot_download_report(two_users):

    response = two_users["other"].get(f"/report/{two_users['case_id']}")

    assert response.status_code == 404


def test_other_user_cannot_delete_case(app, two_users):

    other = two_users["other"]

    token = get_csrf(other, "/")

    response = other.post(
        f"/delete_case/{two_users['case_id']}",
        data={"csrf_token": token}
    )

    assert response.status_code == 404
    assert case_exists(app, two_users["case_id"])


def test_archive_shows_only_own_cases(two_users):

    other_page = two_users["other"].get("/cases").get_data(as_text=True)
    owner_page = two_users["owner"].get("/cases").get_data(as_text=True)

    assert "Пациент Владельца" not in other_page
    assert "Пациент Владельца" in owner_page


def test_archive_search_does_not_leak_other_cases(two_users):

    response = two_users["other"].get(
        "/cases",
        query_string={"patient_name": "Владельца"}
    )

    assert "Пациент Владельца" not in response.get_data(as_text=True)


def test_case_without_owner_is_hidden(app, two_users):

    orphan_id = create_case(app, None, patient_name="Пациент Без Владельца")

    owner = two_users["owner"]

    assert "Пациент Без Владельца" not in owner.get("/cases").get_data(
        as_text=True
    )
    assert owner.get(f"/case/{orphan_id}").status_code == 404
    assert owner.get(f"/report/{orphan_id}").status_code == 404
    assert case_exists(app, orphan_id)


# ---------- Владелец ----------

def test_owner_can_view_case(two_users):

    response = two_users["owner"].get(f"/case/{two_users['case_id']}")

    assert response.status_code == 200
    assert "Пациент Владельца" in response.get_data(as_text=True)


def test_owner_can_download_report_without_public_file(two_users):

    response = two_users["owner"].get(f"/report/{two_users['case_id']}")

    assert response.status_code == 200
    assert response.data[:2] == b"PK"
    assert "attachment" in response.headers["Content-Disposition"]
    assert f"report_{two_users['case_id']}.docx" in response.headers[
        "Content-Disposition"
    ]

    # Отчёт не записывается в публичную папку static
    assert not os.path.exists(os.path.join("static", "report.docx"))


def test_owner_can_delete_case(app, two_users):

    owner = two_users["owner"]

    token = get_csrf(owner, f"/case/{two_users['case_id']}")

    response = owner.post(
        f"/delete_case/{two_users['case_id']}",
        data={"csrf_token": token}
    )

    assert response.status_code == 302
    assert not case_exists(app, two_users["case_id"])


def test_owner_can_submit_analysis(app, two_users):

    owner = two_users["owner"]

    token = get_csrf(owner, "/")

    response = owner.post(
        "/",
        data={
            "csrf_token": token,
            "patient_name": "Новый Пациент",
            "organ": "Желудок",
            "images": [(io.BytesIO(b"fake"), "slide.png")]
        },
        content_type="multipart/form-data"
    )

    assert response.status_code == 200

    page = owner.get("/cases").get_data(as_text=True)

    assert "Новый Пациент" in page
    assert "Новый Пациент" not in two_users["other"].get("/cases").get_data(
        as_text=True
    )


# ---------- CSRF ----------

def test_delete_without_csrf_token_is_rejected(app, two_users):

    response = two_users["owner"].post(f"/delete_case/{two_users['case_id']}")

    assert response.status_code == 400
    assert case_exists(app, two_users["case_id"])


def test_delete_with_wrong_csrf_token_is_rejected(app, two_users):

    response = two_users["owner"].post(
        f"/delete_case/{two_users['case_id']}",
        data={"csrf_token": "0" * 64}
    )

    assert response.status_code == 400
    assert case_exists(app, two_users["case_id"])


def test_login_without_csrf_token_is_rejected(app, client):

    create_user(app, "u", "p")

    response = client.post(
        "/login",
        data={"username": "u", "password": "p"}
    )

    assert response.status_code == 400


def test_forms_contain_csrf_token(app, two_users, client):

    for url in ("/login", "/register", "/consultant-register"):
        get_csrf(client, url)

    get_csrf(two_users["owner"], "/")
    get_csrf(two_users["owner"], f"/case/{two_users['case_id']}")


# ---------- Админка ----------

def test_admin_page_closed_for_anonymous(client):

    response = client.get("/admin/consultants")

    assert response.status_code == 302
    assert response.location.endswith("/login")


def test_admin_page_closed_when_admins_not_configured(two_users, monkeypatch):

    monkeypatch.delenv("PAITHOLOGY_ADMINS", raising=False)

    assert two_users["owner"].get("/admin/consultants").status_code == 403


def test_admin_page_closed_for_non_admin(two_users, monkeypatch):

    monkeypatch.setenv("PAITHOLOGY_ADMINS", "someone_else, owner2")

    assert two_users["owner"].get("/admin/consultants").status_code == 403


def test_admin_page_open_for_admin(two_users, monkeypatch):

    monkeypatch.setenv("PAITHOLOGY_ADMINS", " other , owner ")

    assert two_users["owner"].get("/admin/consultants").status_code == 200


def test_non_admin_cannot_approve_or_reject(app, two_users, monkeypatch):

    monkeypatch.setenv("PAITHOLOGY_ADMINS", "owner")

    consultant_id = create_consultant(app)

    other = two_users["other"]

    token = get_csrf(other, "/")

    assert other.post(
        f"/approve_consultant/{consultant_id}",
        data={"csrf_token": token}
    ).status_code == 403

    assert other.post(
        f"/reject_consultant/{consultant_id}",
        data={"csrf_token": token}
    ).status_code == 403

    with app.app_context():
        consultant = db.session.get(Consultant, consultant_id)
        assert consultant is not None
        assert consultant.verified is False


def test_anonymous_cannot_approve(app, client):

    consultant_id = create_consultant(app)

    token = get_csrf(client, "/login")

    response = client.post(
        f"/approve_consultant/{consultant_id}",
        data={"csrf_token": token}
    )

    assert response.status_code == 302

    with app.app_context():
        assert db.session.get(Consultant, consultant_id).verified is False


def test_approve_by_get_is_not_allowed(app, two_users, monkeypatch):

    monkeypatch.setenv("PAITHOLOGY_ADMINS", "owner")

    consultant_id = create_consultant(app)

    response = two_users["owner"].get(f"/approve_consultant/{consultant_id}")

    assert response.status_code == 405


def test_admin_can_approve(app, two_users, monkeypatch):

    monkeypatch.setenv("PAITHOLOGY_ADMINS", "owner")

    consultant_id = create_consultant(app)

    owner = two_users["owner"]

    token = get_csrf(owner, "/admin/consultants")

    response = owner.post(
        f"/approve_consultant/{consultant_id}",
        data={"csrf_token": token}
    )

    assert response.status_code == 302

    with app.app_context():
        assert db.session.get(Consultant, consultant_id).verified is True


# ---------- Документы консультантов ----------

@pytest.fixture
def document_folders(app, tmp_path, monkeypatch):

    private = tmp_path / "private"
    legacy = tmp_path / "static" / "uploads" / "documents"
    profiles = tmp_path / "static" / "uploads" / "profiles"

    for folder in (private, legacy, profiles):
        folder.mkdir(parents=True)

    (private / "new.pdf").write_bytes(b"new document")
    (private / "foreign.pdf").write_bytes(b"foreign document")
    (legacy / "old.pdf").write_bytes(b"old document")
    (profiles / "photo.png").write_bytes(b"photo")

    monkeypatch.setitem(app.config, "PRIVATE_DOCUMENT_FOLDER", str(private))
    monkeypatch.setitem(app.config, "LEGACY_DOCUMENT_FOLDER", str(legacy))
    monkeypatch.setattr(app, "static_folder", str(tmp_path / "static"))

    return {"private": private, "legacy": legacy}


def test_legacy_documents_not_public(client, document_folders):

    for url in (
        "/static/uploads/documents/old.pdf",
        "/static/uploads/./documents/old.pdf",
        "/static/uploads/profiles/../documents/old.pdf",
    ):
        response = client.get(url)

        assert response.status_code == 404, url
        assert b"old document" not in response.data


def test_other_static_files_still_served(client, document_folders):

    response = client.get("/static/uploads/profiles/photo.png")

    assert response.status_code == 200
    assert response.data == b"photo"


def test_admin_can_open_documents(app, two_users, document_folders, monkeypatch):

    monkeypatch.setenv("PAITHOLOGY_ADMINS", "owner")

    consultant_id = create_consultant(app, documents="new.pdf;old.pdf")

    owner = two_users["owner"]

    new = owner.get(f"/admin/consultants/{consultant_id}/documents/new.pdf")
    old = owner.get(f"/admin/consultants/{consultant_id}/documents/old.pdf")

    assert new.status_code == 200 and new.data == b"new document"
    assert old.status_code == 200 and old.data == b"old document"


def test_admin_cannot_open_unlisted_document(
    app,
    two_users,
    document_folders,
    monkeypatch
):

    monkeypatch.setenv("PAITHOLOGY_ADMINS", "owner")

    consultant_id = create_consultant(app, documents="new.pdf")

    owner = two_users["owner"]

    base = f"/admin/consultants/{consultant_id}/documents/"

    assert owner.get(base + "foreign.pdf").status_code == 404
    assert owner.get(base + "../documents/new.pdf").status_code == 404


def test_non_admin_cannot_open_documents(
    app,
    client,
    two_users,
    document_folders,
    monkeypatch
):

    monkeypatch.setenv("PAITHOLOGY_ADMINS", "owner")

    consultant_id = create_consultant(app, documents="new.pdf")

    url = f"/admin/consultants/{consultant_id}/documents/new.pdf"

    assert two_users["other"].get(url).status_code == 403
    assert client.get(url).status_code == 302


def test_new_documents_saved_privately(app, client, document_folders):

    token = get_csrf(client, "/consultant-register")

    response = client.post(
        "/consultant-register",
        data={
            "csrf_token": token,
            "first_name": "Имя",
            "last_name": "Фамилия",
            "verification_files": [(io.BytesIO(b"diploma"), "diploma.pdf")]
        },
        content_type="multipart/form-data"
    )

    assert response.status_code == 302
    assert (document_folders["private"] / "diploma.pdf").read_bytes() == b"diploma"
    assert not os.path.exists(
        os.path.join("static", "uploads", "documents", "diploma.pdf")
    )


# ---------- Пароли ----------

def register(client, username, password):

    token = get_csrf(client, "/register")

    return client.post(
        "/register",
        data={
            "username": username,
            "password": password,
            "csrf_token": token
        }
    )


def test_new_password_is_hashed(app, client):

    assert register(client, "newuser", "secret-123").status_code == 302

    with app.app_context():
        stored = User.query.filter_by(username="newuser").first().password

    assert stored != "secret-123"
    assert "secret-123" not in stored
    assert app_module.PASSWORD_HASH_PATTERN.match(stored)

    # Колонка User.password имеет длину 100
    assert len(stored) <= 100


def test_new_user_can_login(app, client):

    register(client, "newuser", "secret-123")

    response = login(client, "newuser", "secret-123")

    assert response.status_code == 302
    assert client.get("/cases").status_code == 200


def test_wrong_password_rejected(app, client):

    register(client, "newuser", "secret-123")

    response = login(client, "newuser", "wrong")

    assert response.status_code == 200
    assert client.get("/cases").status_code == 302


def test_hash_cannot_be_used_as_password(app, client):

    register(client, "newuser", "secret-123")

    with app.app_context():
        stored = User.query.filter_by(username="newuser").first().password

    login(client, "newuser", stored)

    assert client.get("/cases").status_code == 302


def test_legacy_plaintext_user_can_still_login(app, client):

    create_user(app, "legacy", "old-pass", hashed=False)

    response = login(client, "legacy", "old-pass")

    assert response.status_code == 302
    assert client.get("/cases").status_code == 200

    # Автоматическая замена старых паролей на хеши пока не выполняется
    with app.app_context():
        assert User.query.filter_by(username="legacy").first().password == "old-pass"


def test_legacy_plaintext_user_wrong_password(app, client):

    create_user(app, "legacy", "old-pass", hashed=False)

    login(client, "legacy", "old-pass2")

    assert client.get("/cases").status_code == 302


def test_register_requires_password(app, client):

    token = get_csrf(client, "/register")

    response = client.post(
        "/register",
        data={"username": "nopass", "csrf_token": token}
    )

    assert response.status_code == 200

    with app.app_context():
        assert User.query.filter_by(username="nopass").first() is None


def test_secret_key_not_hardcoded():

    assert app_module.app.secret_key != "paithology_secret"
