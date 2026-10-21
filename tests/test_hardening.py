"""
Регрессионные тесты второго этапа: защита логинов администраторов,
CSRF с некорректными токенами, конфигурация через переменные окружения,
секретный ключ, выход через POST и безопасные cookie.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys

import pytest

import app as app_module
from database import db, User

from conftest import ROOT, create_user, get_csrf, login


ADMIN = "chief_admin"


def user_exists(app, username):

    with app.app_context():
        return User.query.filter_by(username=username).first() is not None


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


# ---------- 1. Логины администраторов ----------

def test_admin_login_cannot_be_registered_on_empty_db(app, client, monkeypatch):

    monkeypatch.setenv("PAITHOLOGY_ADMINS", ADMIN)

    response = register(client, ADMIN, "attacker-pass")

    assert "недоступен" in response.get_data(as_text=True)
    assert not user_exists(app, ADMIN)

    login(client, ADMIN, "attacker-pass")

    assert client.get("/admin/consultants").status_code == 302


@pytest.mark.parametrize(
    "variant",
    [" chief_admin", "chief_admin ", "CHIEF_ADMIN", "Chief_Admin", "\tchief_admin"]
)
def test_admin_login_variants_cannot_be_registered(app, client, monkeypatch, variant):

    monkeypatch.setenv("PAITHOLOGY_ADMINS", f"other, {ADMIN}")

    response = register(client, variant, "attacker-pass")

    assert "недоступен" in response.get_data(as_text=True)
    assert not user_exists(app, variant)


def test_admin_login_cannot_be_taken_after_db_reset(app, make_client, monkeypatch):

    monkeypatch.setenv("PAITHOLOGY_ADMINS", ADMIN)

    create_user(app, ADMIN, "real-admin-pass")

    # Сброс базы, как при потере временного диска
    with app.app_context():
        db.drop_all()
        db.create_all()

    attacker = make_client()

    response = register(attacker, ADMIN, "attacker-pass")

    assert "недоступен" in response.get_data(as_text=True)
    assert not user_exists(app, ADMIN)

    # Обычная учётная запись постороннего не получает админских прав
    register(attacker, "attacker", "attacker-pass")
    login(attacker, "attacker", "attacker-pass")

    assert attacker.get("/cases").status_code == 200
    assert attacker.get("/admin/consultants").status_code == 403


def test_admin_list_change_does_not_grant_rights_to_lookalike(
    app,
    make_client,
    monkeypatch
):

    # Похожий логин, зарегистрированный до того, как задан список админов
    create_user(app, "Chief_Admin", "lookalike-pass")

    monkeypatch.setenv("PAITHOLOGY_ADMINS", ADMIN)

    client = make_client()
    login(client, "Chief_Admin", "lookalike-pass")

    assert client.get("/admin/consultants").status_code == 403


def test_ordinary_registration_still_works(app, client, monkeypatch):

    monkeypatch.setenv("PAITHOLOGY_ADMINS", ADMIN)

    assert register(client, "doctor", "doctor-pass").status_code == 302
    assert user_exists(app, "doctor")


def test_existing_admin_account_keeps_access(app, client, monkeypatch):

    create_user(app, ADMIN, "real-admin-pass")

    monkeypatch.setenv("PAITHOLOGY_ADMINS", ADMIN)

    login(client, ADMIN, "real-admin-pass")

    assert client.get("/admin/consultants").status_code == 200


def test_create_admin_command(app, client, monkeypatch):

    monkeypatch.setenv("PAITHOLOGY_ADMINS", ADMIN)

    result = app.test_cli_runner().invoke(
        args=["create-admin", ADMIN],
        input="Adm1n-pass\nAdm1n-pass\n"
    )

    assert result.exit_code == 0, result.output
    assert "Adm1n-pass" not in result.output

    with app.app_context():
        stored = User.query.filter_by(username=ADMIN).first().password

    assert app_module.PASSWORD_HASH_PATTERN.match(stored)

    login(client, ADMIN, "Adm1n-pass")

    assert client.get("/admin/consultants").status_code == 200


def test_create_admin_rejects_unlisted_login(app, monkeypatch):

    monkeypatch.setenv("PAITHOLOGY_ADMINS", ADMIN)

    result = app.test_cli_runner().invoke(
        args=["create-admin", "someone"],
        input="pass\npass\n"
    )

    assert result.exit_code != 0
    assert not user_exists(app, "someone")


def test_create_admin_does_not_touch_existing_user(app, monkeypatch):

    create_user(app, ADMIN, "old-pass", hashed=False)

    monkeypatch.setenv("PAITHOLOGY_ADMINS", ADMIN)

    result = app.test_cli_runner().invoke(
        args=["create-admin", ADMIN],
        input="new-pass\nnew-pass\n"
    )

    assert result.exit_code != 0

    with app.app_context():
        assert User.query.filter_by(username=ADMIN).first().password == "old-pass"


def test_create_admin_without_admins_configured(app, monkeypatch):

    monkeypatch.delenv("PAITHOLOGY_ADMINS", raising=False)

    result = app.test_cli_runner().invoke(
        args=["create-admin", ADMIN],
        input="pass\npass\n"
    )

    assert result.exit_code != 0
    assert not user_exists(app, ADMIN)


# ---------- 2. CSRF с некорректными токенами ----------

BAD_TOKENS = ["жжж", "токен" * 13, "☃" * 64, "é" * 64, "a" * 10000, "\x00"]


@pytest.mark.parametrize("bad", BAD_TOKENS)
def test_bad_csrf_token_returns_400(app, client, bad):

    create_user(app, "u", "p")

    # В сессии уже есть настоящий токен, поэтому выполняется само сравнение
    get_csrf(client, "/login")

    for url, data in [
        ("/login", {"username": "u", "password": "p"}),
        ("/register", {"username": "new", "password": "p"}),
        ("/logout", {}),
        ("/delete_case/1", {}),
        ("/approve_consultant/1", {}),
    ]:
        response = client.post(url, data={**data, "csrf_token": bad})

        assert response.status_code == 400, url

    assert not user_exists(app, "new")


def test_non_ascii_csrf_header_returns_400(client):

    get_csrf(client, "/login")

    response = client.post(
        "/logout",
        headers={"X-CSRF-Token": "é" * 64}
    )

    assert response.status_code == 400


def test_csrf_token_matches_helper():

    match = app_module.csrf_token_matches

    assert match("abc", "abc")
    assert not match("жжж", "abc")
    assert not match("", "abc")
    assert not match("abc", None)
    assert not match(None, "abc")


def test_new_key_file_created_with_owner_only_permissions(tmp_path):

    key_path = tmp_path / "secret_key"

    old_umask = os.umask(0)

    try:
        app_module.create_secret_key_file(str(key_path))
    finally:
        os.umask(old_umask)

    assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600
    assert len(key_path.read_text()) == 64
    assert [p.name for p in tmp_path.iterdir()] == ["secret_key"]


def test_create_key_file_does_not_overwrite_existing(tmp_path):

    key_path = tmp_path / "secret_key"
    key_path.write_text("x" * 64)

    app_module.create_secret_key_file(str(key_path))

    assert key_path.read_text() == "x" * 64
    assert [p.name for p in tmp_path.iterdir()] == ["secret_key"]


# ---------- 4. Выход через POST ----------

def test_logout_by_get_is_not_allowed(app, client):

    create_user(app, "u", "p")
    login(client, "u", "p")

    assert client.get("/logout").status_code == 405
    assert client.get("/cases").status_code == 200


def test_logout_without_token_is_rejected(app, client):

    create_user(app, "u", "p")
    login(client, "u", "p")

    assert client.post("/logout").status_code == 400
    assert client.get("/cases").status_code == 200


def test_logout_with_token(app, client):

    create_user(app, "u", "p")
    login(client, "u", "p")

    token = get_csrf(client, "/cases")

    response = client.post("/logout", data={"csrf_token": token})

    assert response.status_code == 302
    assert client.get("/cases").status_code == 302


def test_sidebar_contains_logout_form(app, client):

    create_user(app, "u", "p")
    login(client, "u", "p")

    page = client.get("/cases").get_data(as_text=True)

    assert 'id="logout-form" method="POST" action="/logout"' in page


# ---------- 5. Cookie ----------

def test_secure_cookie_sent_only_when_enabled(app, client, monkeypatch):

    monkeypatch.setitem(app.config, "SESSION_COOKIE_SECURE", True)

    response = client.get("/login", base_url="https://localhost")

    cookie = response.headers.get("Set-Cookie", "")

    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_login_works_over_https_with_secure_cookie(app, monkeypatch):

    monkeypatch.setitem(app.config, "SESSION_COOKIE_SECURE", True)

    create_user(app, "u", "p")

    client = app.test_client()

    page = client.get("/login", base_url="https://localhost").get_data(as_text=True)

    token = re.search(r'name="csrf_token" value="([0-9a-f]+)"', page).group(1)

    response = client.post(
        "/login",
        data={"username": "u", "password": "p", "csrf_token": token},
        base_url="https://localhost"
    )

    assert response.status_code == 302
    assert client.get("/cases", base_url="https://localhost").status_code == 200


def test_cookie_not_secure_in_tests_by_default(app, client):

    cookie = client.get("/login").headers.get("Set-Cookie", "")

    assert "Secure" not in cookie
    assert "HttpOnly" in cookie


# ---------- 3. Конфигурация и секретный ключ (отдельные процессы) ----------

PROBE = r"""
import json, os, stat, sys, types
model = types.ModuleType("model")
model.analyze = lambda *a: None
sys.modules["model"] = model
sys.path.insert(0, os.getcwd())
try:
    import app as m
except Exception as e:
    print("STARTUP_ERROR " + type(e).__name__ + ": " + str(e))
    sys.exit(3)
key_file = os.path.join(m.app.instance_path, "secret_key")
client = m.app.test_client()
print("RESULT " + json.dumps({
    "db": m.app.config["SQLALCHEMY_DATABASE_URI"],
    "private": os.path.relpath(m.app.config["PRIVATE_DOCUMENT_FOLDER"], os.getcwd()),
    "secure": m.app.config["SESSION_COOKIE_SECURE"],
    "key_len": len(m.app.secret_key),
    "key_file_mode": oct(stat.S_IMODE(os.stat(key_file).st_mode)) if os.path.exists(key_file) else None,
    "login_status": client.get("/login").status_code,
}))
"""

ENV_NAMES = [
    "PAITHOLOGY_SECRET_KEY",
    "PAITHOLOGY_DATABASE_URI",
    "PAITHOLOGY_PRIVATE_UPLOADS",
    "PAITHOLOGY_ADMINS",
    "PAITHOLOGY_SECURE_COOKIES",
    "RENDER",
]


@pytest.fixture
def isolated_app(tmp_path):

    # Копия приложения: тесты не создают и не трогают instance/ проекта
    for name in ("app.py", "database.py", "doc_generator.py", "qr_generator.py"):
        shutil.copy(os.path.join(ROOT, name), tmp_path / name)

    shutil.copytree(os.path.join(ROOT, "templates"), tmp_path / "templates")

    return tmp_path


def run_probe(folder, env=None):

    full_env = {
        k: v for k, v in os.environ.items() if k not in ENV_NAMES
    }
    full_env.update(env or {})
    full_env["PYTHONDONTWRITEBYTECODE"] = "1"

    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=folder,
        env=full_env,
        capture_output=True,
        text=True,
        timeout=120
    )

    output = result.stdout + result.stderr

    for line in result.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT "):]), output, result.returncode

    return None, output, result.returncode


def test_starts_without_any_variables(isolated_app):

    data, output, code = run_probe(isolated_app)

    assert code == 0, output
    assert data["db"] == "sqlite:///paithology.db"
    assert data["private"] == os.path.join("private_uploads", "documents")
    assert data["secure"] is False
    assert data["login_status"] == 200
    assert data["key_len"] == 64
    assert data["key_file_mode"] == "0o600"


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_database_uri_uses_default(isolated_app, value):

    data, output, code = run_probe(
        isolated_app,
        {"PAITHOLOGY_DATABASE_URI": value}
    )

    assert code == 0, output
    assert data["db"] == "sqlite:///paithology.db"
    assert data["login_status"] == 200


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_private_uploads_uses_default(isolated_app, value):

    data, output, code = run_probe(
        isolated_app,
        {"PAITHOLOGY_PRIVATE_UPLOADS": value}
    )

    assert code == 0, output
    assert data["private"] == os.path.join("private_uploads", "documents")
    assert not (isolated_app / "documents").exists()


def test_key_file_is_stable_between_restarts(isolated_app):

    run_probe(isolated_app)

    first = (isolated_app / "instance" / "secret_key").read_text()

    data, output, code = run_probe(isolated_app)

    assert code == 0, output
    assert (isolated_app / "instance" / "secret_key").read_text() == first
    assert first not in output


@pytest.mark.parametrize("content", ["", "   \n", "short"])
def test_bad_key_file_stops_startup_with_clear_message(isolated_app, content):

    instance = isolated_app / "instance"
    instance.mkdir()
    (instance / "secret_key").write_text(content)

    data, output, code = run_probe(isolated_app)

    assert data is None
    assert code == 3
    assert "SecretKeyError" in output
    assert "пустой или повреждён" in output

    # Файл не перезаписывается автоматически
    assert (instance / "secret_key").read_text() == content


def test_valid_key_file_with_open_permissions_is_restricted(isolated_app):

    instance = isolated_app / "instance"
    instance.mkdir()

    key = "k" * 64
    (instance / "secret_key").write_text(key)
    os.chmod(instance / "secret_key", 0o644)

    data, output, code = run_probe(isolated_app)

    assert code == 0, output
    assert data["key_file_mode"] == "0o600"
    assert (instance / "secret_key").read_text() == key
    assert key not in output


def test_unusable_instance_folder_gives_clear_error(isolated_app):

    # instance — обычный файл, поэтому создать в нём ключ невозможно
    (isolated_app / "instance").write_text("not a folder")

    data, output, code = run_probe(isolated_app)

    assert data is None
    assert code == 3
    assert "SecretKeyError" in output
    assert "PAITHOLOGY_SECRET_KEY" in output


def test_env_key_used_and_not_logged(isolated_app):

    key = "e" * 48

    data, output, code = run_probe(
        isolated_app,
        {"PAITHOLOGY_SECRET_KEY": key}
    )

    assert code == 0, output
    assert data["key_len"] == 48
    assert not (isolated_app / "instance" / "secret_key").exists()
    assert key not in output


def test_short_env_key_stops_startup(isolated_app):

    key = "too-short-key"

    data, output, code = run_probe(
        isolated_app,
        {"PAITHOLOGY_SECRET_KEY": key}
    )

    assert data is None
    assert code == 3
    assert "слишком короткий" in output
    assert key not in output


def test_blank_env_key_falls_back_to_file(isolated_app):

    data, output, code = run_probe(
        isolated_app,
        {"PAITHOLOGY_SECRET_KEY": "   "}
    )

    assert code == 0, output
    assert data["key_len"] == 64


@pytest.mark.parametrize(
    "env, expected",
    [
        ({}, False),
        ({"RENDER": "true"}, True),
        ({"RENDER": "true", "PAITHOLOGY_SECURE_COOKIES": "0"}, False),
        ({"PAITHOLOGY_SECURE_COOKIES": "1"}, True),
        ({"PAITHOLOGY_SECURE_COOKIES": ""}, False),
        ({"RENDER": "true", "PAITHOLOGY_SECURE_COOKIES": " "}, True),
    ]
)
def test_secure_cookie_setting(isolated_app, env, expected):

    data, output, code = run_probe(isolated_app, env)

    assert code == 0, output
    assert data["secure"] is expected
