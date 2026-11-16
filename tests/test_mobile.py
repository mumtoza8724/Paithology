"""
Мобильная версия: на всех страницах есть viewport и мобильные стили,
а снимки гистопрепарата можно сделать камерой телефона (главная страница
и фото в протоколе), сохранив обычную загрузку файлов.
"""

import os
import re

import pytest

from conftest import ROOT, create_user, login


MOBILE_CSS = "/static/mobile.css"


@pytest.fixture
def doctor(app, client):

    user_id = create_user(app, "doctor", "doctor-pass")
    login(client, "doctor", "doctor-pass")
    client.user_id = user_id

    return client


def page(client, url):

    response = client.get(url)
    assert response.status_code == 200, url

    return response.get_data(as_text=True)


def assert_mobile_ready(html, url):

    assert re.search(r'<meta name="viewport"\s+content="width=device-width', html), url
    assert MOBILE_CSS in html, url


def test_public_pages_have_viewport(client):

    for url in ("/login", "/register"):
        html = page(client, url)
        assert 'name="viewport"' in html, url


def test_pages_with_menu_are_mobile_ready(doctor):

    urls = [
        "/",
        "/cases",
        "/consultants",
        "/consultant-register",
        "/protocols/new",
        "/protocols/new/small",
        "/protocols/new/large",
        "/protocols/archive",
        "/settings",
    ]

    for url in urls:
        assert_mobile_ready(page(doctor, url), url)


def test_result_page_is_mobile_ready(doctor):

    # Страница результата открывается после анализа (POST на главную);
    # нейросеть в тестах заменена заглушкой
    from conftest import get_csrf
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

    assert response.status_code == 200
    assert_mobile_ready(response.get_data(as_text=True), "result")


def test_admin_consultants_is_mobile_ready(doctor, monkeypatch):

    monkeypatch.setenv("PAITHOLOGY_ADMINS", "doctor")

    assert_mobile_ready(page(doctor, "/admin/consultants"), "/admin/consultants")


def test_main_page_has_camera_and_file_upload(doctor):

    html = page(doctor, "/")

    # обычная загрузка файлов осталась
    assert 'id="images"' in html and 'name="images"' in html

    # отдельное поле камеры: без name, чтобы не отправляться дважды
    camera = re.search(r'<input[^>]*id="camera-input"[^>]*>', html, re.S)
    assert camera, "нет поля камеры"
    assert 'capture="environment"' in camera.group(0)
    assert 'accept="image/*"' in camera.group(0)
    assert "name=" not in camera.group(0)

    assert 'id="camera-btn"' in html and "Сделать фото" in html
    assert 'id="pick-btn"' in html


def test_protocol_photo_slots_have_camera(doctor):

    small = page(doctor, "/protocols/new/small")
    large = page(doctor, "/protocols/new/large")

    for html in (small, large):
        cameras = re.findall(r'<input[^>]*data-photo-camera[^>]*>', html)
        # фото микропрепарата в шаблоне заключения
        assert cameras, "нет камеры у фото микропрепарата"
        for tag in cameras:
            assert 'capture="environment"' in tag
            assert "name=" not in tag and "data-field" not in tag
        assert "data-photo-take" in html and "data-photo-pick" in html
        # обычные поля файлов на месте
        assert 'data-field="photo"' in html

    # у большого протокола есть ещё фото макропрепарата
    assert len(re.findall(r"data-photo-camera", large)) == 2
    assert 'name="macro_photo"' in large


def test_mobile_css_only_targets_small_screens():

    with open(os.path.join(ROOT, "static", "mobile.css"), encoding="utf-8") as f:
        css = f.read()

    # вне медиазапросов — только скрытие мобильных кнопок и их оформление
    outside = re.split(r"@media", css)[0]
    for selector in (".main", ".form-grid", ".sidebar", "body"):
        assert re.search(r"(^|\n)\s*" + re.escape(selector) + r"\s*[{,]", outside) is None, selector

    assert "@media (max-width:768px)" in css


def test_photo_slot_script_skips_camera_input():

    with open(os.path.join(ROOT, "static", "protocol.js"), encoding="utf-8") as f:
        script = f.read()

    assert 'input[type=file]:not([data-photo-camera])' in script
    assert "[data-photo-camera]" in script
