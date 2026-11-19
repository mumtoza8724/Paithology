"""
Консультации: «Мои консультации», «Входящие запросы», уведомления.

Сценарий: пользователь отправляет своё исследование консультанту →
консультант принимает (оплата в тестовом режиме) → отвечает. На каждом шаге
вторая сторона получает уведомление. Чужие запросы недоступны.

Только вымышленные данные.
"""

import io
import re
from datetime import timedelta

import pytest

import consultations
from database import (
    db,
    Case,
    Consultant,
    ConsultantAccount,
    ConsultationRequest,
    Notification,
)

from conftest import create_case, create_user, get_csrf, login


@pytest.fixture
def people(app, make_client):

    """Пользователь с исследованием, консультант с проверенной анкетой и посторонний."""

    requester_id = create_user(app, "doctor", "doctor-pass")
    consultant_user_id = create_user(app, "expert", "expert-pass")
    stranger_id = create_user(app, "stranger", "stranger-pass")

    case_id = create_case(app, requester_id, patient_name="Тестов И")

    with app.app_context():
        consultant = Consultant(
            first_name="Эксперт", last_name="Тестовый", verified=True,
            consult_price="50", response_time="24",
        )
        db.session.add(consultant)
        db.session.flush()
        db.session.add(ConsultantAccount(consultant_id=consultant.id, user_id=consultant_user_id))
        db.session.commit()
        consultant_id = consultant.id

    clients = {}

    for name, password in (("doctor", "doctor-pass"), ("expert", "expert-pass"), ("stranger", "stranger-pass")):
        client = make_client()
        login(client, name, password)
        clients[name] = client

    return {
        **clients,
        "requester_id": requester_id,
        "consultant_user_id": consultant_user_id,
        "stranger_id": stranger_id,
        "case_id": case_id,
        "consultant_id": consultant_id,
    }


def send(client, consultant_id, case_id, question="Подтвердите, пожалуйста, диагноз"):

    token = get_csrf(client, f"/consultations/new?consultant={consultant_id}")

    return client.post("/consultations/new", data={
        "csrf_token": token,
        "consultant": consultant_id,
        "case": case_id,
        "question": question,
    })


def act(client, request_id, action, **data):

    token = get_csrf(client, f"/consultations/{request_id}")

    return client.post(f"/consultations/{request_id}/{action}", data={"csrf_token": token, **data})


def last_request(app):

    with app.app_context():
        return db.session.query(ConsultationRequest).order_by(ConsultationRequest.id.desc()).first()


def notifications_of(app, user_id):

    with app.app_context():
        return [
            (n.kind, n.read_at is not None)
            for n in Notification.query.filter_by(user_id=user_id).order_by(Notification.id)
        ]


# ---------- Полный сценарий ----------

def test_full_flow_with_notifications(app, people):

    response = send(people["doctor"], people["consultant_id"], people["case_id"])

    assert response.status_code == 302
    item = last_request(app)
    assert item.status == "sent"
    assert item.price == "50" and item.response_hours == 24
    assert item.payment_status == "none"

    # консультанту пришло уведомление о новом запросе
    assert notifications_of(app, people["consultant_user_id"]) == [("new_request", False)]

    incoming = people["expert"].get("/consultations/incoming").get_data(as_text=True)
    assert "doctor" in incoming and "Новый" in incoming

    # принятие: оплата отмечена, срок ответа по анкете
    assert act(people["expert"], item.id, "accept").status_code == 302
    item = last_request(app)
    assert item.status == "accepted"
    assert item.payment_status == "paid"
    assert item.due_at - item.accepted_at == timedelta(hours=24)
    assert ("accepted", False) in notifications_of(app, people["requester_id"])

    # ответ
    assert act(people["expert"], item.id, "answer", answer_conclusion="Аденокарцинома G2", answer_comment="Рекомендую ИГХ").status_code == 302
    item = last_request(app)
    assert item.status == "answered"
    assert ("answered", False) in notifications_of(app, people["requester_id"])

    page = people["doctor"].get(f"/consultations/{item.id}").get_data(as_text=True)
    assert "Аденокарцинома G2" in page and "Рекомендую ИГХ" in page

    # открытие запроса отмечает уведомления о нём прочитанными
    assert all(read for _kind, read in notifications_of(app, people["requester_id"]))

    mine = people["doctor"].get("/consultations?status=answered").get_data(as_text=True)
    assert "Эксперт Тестовый" in mine


def test_consultant_can_ask_another_consultant(app, people):

    # у консультанта тоже есть «Мои консультации»: второе мнение коллеги
    second_user = create_user(app, "expert2", "expert2-pass")
    case_id = create_case(app, people["consultant_user_id"])

    with app.app_context():
        other = Consultant(first_name="Второй", last_name="Эксперт", verified=True, response_time="6")
        db.session.add(other)
        db.session.flush()
        db.session.add(ConsultantAccount(consultant_id=other.id, user_id=second_user))
        db.session.commit()
        other_id = other.id

    assert send(people["expert"], other_id, case_id).status_code == 302
    assert "Второй Эксперт" in people["expert"].get("/consultations").get_data(as_text=True)


# ---------- Доступ ----------

def test_stranger_cannot_see_or_act(app, people):

    send(people["doctor"], people["consultant_id"], people["case_id"])
    item = last_request(app)

    assert people["stranger"].get(f"/consultations/{item.id}").status_code == 404

    token = get_csrf(people["stranger"], "/consultations")
    for action in ("accept", "decline", "cancel", "answer"):
        response = people["stranger"].post(f"/consultations/{item.id}/{action}", data={"csrf_token": token})
        assert response.status_code == 404, action

    assert last_request(app).status == "sent"


def test_roles_are_enforced(app, people):

    send(people["doctor"], people["consultant_id"], people["case_id"])
    item = last_request(app)

    # отправитель не может принять свой запрос, консультант — отменить чужой
    assert act(people["doctor"], item.id, "accept").status_code == 404
    assert act(people["doctor"], item.id, "answer", answer_conclusion="x").status_code == 404
    assert act(people["expert"], item.id, "cancel").status_code == 404
    assert last_request(app).status == "sent"


def test_actions_require_csrf(app, people):

    send(people["doctor"], people["consultant_id"], people["case_id"])
    item = last_request(app)

    assert people["expert"].post(f"/consultations/{item.id}/accept").status_code == 400
    assert last_request(app).status == "sent"


def test_cannot_send_foreign_case(app, people):

    foreign_case = create_case(app, people["stranger_id"])

    response = send(people["doctor"], people["consultant_id"], foreign_case)

    assert response.status_code == 400
    assert last_request(app) is None


def test_cannot_send_to_self(app, people):

    own_case = create_case(app, people["consultant_user_id"])

    response = send(people["expert"], people["consultant_id"], own_case)

    assert response.status_code == 400
    assert last_request(app) is None


def test_duplicate_open_request_is_rejected(app, people):

    send(people["doctor"], people["consultant_id"], people["case_id"])

    response = send(people["doctor"], people["consultant_id"], people["case_id"])

    assert response.status_code == 400
    assert "уже отправлено" in response.get_data(as_text=True)


def test_patient_name_hidden_from_consultant(app, people):

    send(people["doctor"], people["consultant_id"], people["case_id"])
    item = last_request(app)

    assert "Тестов И" not in people["expert"].get(f"/consultations/{item.id}").get_data(as_text=True)
    assert "Тестов И" in people["doctor"].get(f"/consultations/{item.id}").get_data(as_text=True)


# ---------- Отмена, отклонение, ограничения ----------

def test_cancel_only_before_acceptance(app, people):

    send(people["doctor"], people["consultant_id"], people["case_id"])
    item = last_request(app)

    act(people["expert"], item.id, "accept")
    act(people["doctor"], item.id, "cancel")

    assert last_request(app).status == "accepted"


def test_cancel_notifies_consultant(app, people):

    send(people["doctor"], people["consultant_id"], people["case_id"])
    item = last_request(app)

    act(people["doctor"], item.id, "cancel")

    assert last_request(app).status == "cancelled"
    assert ("cancelled", False) in notifications_of(app, people["consultant_user_id"])
    # отменённые не показываются во входящих
    assert f"/consultations/{item.id}" not in people["expert"].get("/consultations/incoming").get_data(as_text=True)


def test_decline_notifies_requester(app, people):

    send(people["doctor"], people["consultant_id"], people["case_id"])
    item = last_request(app)

    act(people["expert"], item.id, "decline")

    item = last_request(app)
    assert item.status == "declined" and item.payment_status == "none"
    assert ("declined", False) in notifications_of(app, people["requester_id"])


def test_answer_requires_acceptance_and_text(app, people):

    send(people["doctor"], people["consultant_id"], people["case_id"])
    item = last_request(app)

    act(people["expert"], item.id, "answer", answer_conclusion="Рано")
    assert last_request(app).status == "sent"

    act(people["expert"], item.id, "accept")
    act(people["expert"], item.id, "answer", answer_conclusion="   ")
    assert last_request(app).status == "accepted"


def test_overdue_is_shown(app, people):

    send(people["doctor"], people["consultant_id"], people["case_id"])
    item = last_request(app)
    act(people["expert"], item.id, "accept")

    with app.app_context():
        stored = db.session.get(ConsultationRequest, item.id)
        stored.due_at = consultations.utcnow() - timedelta(hours=1)
        db.session.commit()

    assert "Срок ответа истёк" in people["doctor"].get("/consultations").get_data(as_text=True)


# ---------- Страницы и меню ----------

def test_incoming_page_for_non_consultant(people):

    page = people["doctor"].get("/consultations/incoming").get_data(as_text=True)

    assert "Раздел для консультантов" in page


def test_menu_shows_incoming_only_for_consultants(people):

    doctor_menu = people["doctor"].get("/consultations").get_data(as_text=True)
    expert_menu = people["expert"].get("/consultations").get_data(as_text=True)

    assert "Мои консультации" in doctor_menu and "Мои консультации" in expert_menu
    assert "/consultations/incoming" not in doctor_menu
    assert "/consultations/incoming" in expert_menu


def test_pages_require_login(client):

    for url in ("/consultations", "/consultations/incoming", "/notifications", "/notifications/feed"):
        response = client.get(url)
        assert response.status_code == 302 and response.location.endswith("/login"), url


def test_bell_only_for_signed_in_users(client, people):

    assert 'class="notify"' not in client.get("/login").get_data(as_text=True)
    assert 'class="notify"' in people["doctor"].get("/").get_data(as_text=True)


def test_consultant_card_links_to_request_form(people):

    page = people["doctor"].get(f"/consultants?case={people['case_id']}").get_data(as_text=True)

    assert f"/consultations/new?consultant={people['consultant_id']}&amp;case={people['case_id']}" in page
    assert "Выберите консультанта" in page


def test_unlinked_consultant_does_not_accept_requests(app, people):

    with app.app_context():
        old = Consultant(first_name="Старая", last_name="Анкета", verified=True)
        db.session.add(old)
        db.session.commit()
        old_id = old.id

    page = people["doctor"].get("/consultants").get_data(as_text=True)
    assert "Пока не принимает запросы" in page

    response = send(people["doctor"], old_id, people["case_id"])
    assert response.status_code == 400


# ---------- Уведомления ----------

def test_notification_feed_open_and_read_all(app, people):

    send(people["doctor"], people["consultant_id"], people["case_id"])

    feed = people["expert"].get("/notifications/feed").get_json()
    assert feed["unread"] == 1
    assert feed["items"][0]["text"] == "Новый запрос на консультацию от doctor"

    # чужое уведомление открыть нельзя
    other_id = int(re.search(r"/notifications/(\d+)/open", feed["items"][0]["url"]).group(1))
    assert people["stranger"].get(f"/notifications/{other_id}/open").status_code == 404

    response = people["expert"].get(feed["items"][0]["url"])
    assert response.status_code == 302 and "/consultations/" in response.location
    assert people["expert"].get("/notifications/feed").get_json()["unread"] == 0

    item = last_request(app)
    act(people["expert"], item.id, "accept")
    assert people["doctor"].get("/notifications/feed").get_json()["unread"] == 1

    token = get_csrf(people["doctor"], "/notifications")
    people["doctor"].post("/notifications/read-all", data={"csrf_token": token})
    assert people["doctor"].get("/notifications/feed").get_json()["unread"] == 0


def test_notification_text_follows_language(app, people):

    send(people["doctor"], people["consultant_id"], people["case_id"])

    people["expert"].get("/lang/en?next=/")
    feed = people["expert"].get("/notifications/feed").get_json()

    assert feed["items"][0]["text"] == "New consultation request from doctor"


# ---------- Анкета консультанта ----------

def register_profile(client):

    token = get_csrf(client, "/consultant-register")

    return client.post("/consultant-register", data={
        "csrf_token": token,
        "first_name": "Новый",
        "last_name": "Консультант",
        "response_time": "6",
        "consult_price": "40",
    }, content_type="multipart/form-data")


def test_registration_links_profile_to_account(app, people):

    assert register_profile(people["stranger"]).status_code == 302

    with app.app_context():
        link = ConsultantAccount.query.filter_by(user_id=people["stranger_id"]).first()
        assert link is not None
        assert db.session.get(Consultant, link.consultant_id).verified is False

    # анкета на проверке: входящие ещё недоступны, повторно отправить нельзя
    assert "Анкета на проверке" in people["stranger"].get("/consultations/incoming").get_data(as_text=True)
    assert register_profile(people["stranger"]).status_code == 409


def test_rejecting_consultant_closes_open_requests(app, people, monkeypatch):

    monkeypatch.setenv("PAITHOLOGY_ADMINS", "stranger")

    send(people["doctor"], people["consultant_id"], people["case_id"])

    token = get_csrf(people["stranger"], "/admin/consultants")
    response = people["stranger"].post(f"/reject_consultant/{people['consultant_id']}", data={"csrf_token": token})

    assert response.status_code == 302

    item = last_request(app)
    assert item.status == "declined"
    assert ("consultant_removed", False) in notifications_of(app, people["requester_id"])

    with app.app_context():
        assert ConsultantAccount.query.filter_by(consultant_id=people["consultant_id"]).first() is None

    # запрос остаётся в «Моих консультациях» и открывается
    assert people["doctor"].get(f"/consultations/{item.id}").status_code == 200


def test_admin_links_old_profile(app, people, monkeypatch):

    monkeypatch.setenv("PAITHOLOGY_ADMINS", "stranger")

    with app.app_context():
        old = Consultant(first_name="Старая", last_name="Анкета", verified=True)
        db.session.add(old)
        db.session.commit()
        old_id = old.id

    new_user = create_user(app, "oldexpert", "oldexpert-pass")

    token = get_csrf(people["stranger"], "/admin/consultants")
    people["stranger"].post(f"/admin/consultants/{old_id}/link", data={"csrf_token": token, "username": "oldexpert"})

    with app.app_context():
        assert ConsultantAccount.query.filter_by(consultant_id=old_id).first().user_id == new_user

    # к аккаунту с анкетой вторую не привязать
    people["stranger"].post(f"/admin/consultants/{old_id}/link", data={"csrf_token": token, "username": "expert"})
    page = people["stranger"].get("/admin/consultants").get_data(as_text=True)
    assert "уже привязана" in page


def test_deleted_case_keeps_request_readable(app, people):

    send(people["doctor"], people["consultant_id"], people["case_id"])
    item = last_request(app)

    token = get_csrf(people["doctor"], f"/case/{people['case_id']}")
    people["doctor"].post(f"/delete_case/{people['case_id']}", data={"csrf_token": token})

    page = people["expert"].get(f"/consultations/{item.id}").get_data(as_text=True)
    assert "Исследование удалено отправителем." in page
