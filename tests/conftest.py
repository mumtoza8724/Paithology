"""
Общая настройка тестов доступа.

Тесты работают на временной SQLite-базе во временной папке и не трогают
рабочую базу, загрузки и модель. Нейросеть в тестах не нужна, поэтому модуль
model подменяется заглушкой (TensorFlow не загружается).
"""

import os
import re
import sys
import tempfile
import types

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TMP_DIR = tempfile.mkdtemp(prefix="paithology_tests_")

os.environ["PAITHOLOGY_DATABASE_URI"] = "sqlite:///" + os.path.join(
    TMP_DIR,
    "test.db"
)
os.environ["PAITHOLOGY_SECRET_KEY"] = "test-secret-key-for-paithology-tests-0123456789"
os.environ.pop("PAITHOLOGY_SECURE_COOKIES", None)
os.environ.pop("RENDER", None)
os.environ["PAITHOLOGY_PRIVATE_UPLOADS"] = os.path.join(
    TMP_DIR,
    "private_uploads"
)
os.environ.pop("PAITHOLOGY_ADMINS", None)


def fake_analyze(image_paths, organ, clinical, gender):

    return (
        "Тестовое описание",
        "Тестовое заключение",
        image_paths[0] if image_paths else "",
        50.0,
        100.0,
        len(image_paths),
        len(image_paths),
        ["Тестовый признак"]
    )


model_stub = types.ModuleType("model")
model_stub.analyze = fake_analyze
sys.modules["model"] = model_stub

# Относительные пути загрузок (static/uploads) создаются во временной папке
os.chdir(TMP_DIR)
sys.path.insert(0, ROOT)

import app as app_module  # noqa: E402

from database import db, Case, Consultant, User  # noqa: E402


CSRF_RE = re.compile(r'name="csrf_token" value="([0-9a-f]+)"')


@pytest.fixture
def app():

    flask_app = app_module.app
    flask_app.config["TESTING"] = True

    with flask_app.app_context():
        db.drop_all()
        db.create_all()

    yield flask_app

    with flask_app.app_context():
        db.session.remove()


@pytest.fixture
def client(app):

    return app.test_client()


@pytest.fixture
def make_client(app):

    return app.test_client


def get_csrf(client, url="/login"):

    response = client.get(url)

    match = CSRF_RE.search(response.get_data(as_text=True))

    assert match, f"На странице {url} нет csrf_token"

    return match.group(1)


def create_user(app, username, password, hashed=True):

    with app.app_context():

        stored = app_module.hash_password(password) if hashed else password

        user = User(username=username, password=stored)

        db.session.add(user)
        db.session.commit()

        return user.id


def create_case(app, user_id, patient_name="Пациент"):

    with app.app_context():

        case = Case(
            user_id=user_id,
            patient_name=patient_name,
            organ="Желудок",
            description="Описание",
            conclusion="Заключение",
            images=""
        )

        db.session.add(case)
        db.session.commit()

        return case.id


def create_consultant(app, documents=""):

    with app.app_context():

        consultant = Consultant(
            first_name="Имя",
            last_name="Фамилия",
            documents=documents,
            verified=False
        )

        db.session.add(consultant)
        db.session.commit()

        return consultant.id


def login(client, username, password):

    token = get_csrf(client, "/login")

    return client.post(
        "/login",
        data={
            "username": username,
            "password": password,
            "csrf_token": token
        }
    )


def case_exists(app, case_id):

    with app.app_context():
        return db.session.get(Case, case_id) is not None
