"""
Консультации: запросы второго мнения консультантам и уведомления.

Порядок работы:
  1. Пользователь (в том числе консультант) отправляет своё исследование
     ИИ-анализа выбранному консультанту — статус «sent».
  2. Консультант принимает запрос (статус «accepted»): фиксируется оплата и
     срок ответа по времени, которое консультант указал в анкете. Пока
     оплата без реального перевода денег — платёжная система подключается
     отдельно (см. PAYMENT_MODE).
  3. Консультант отвечает (статус «answered»).
  Консультант может отклонить запрос («declined»), пользователь — отменить
  его, пока он не принят («cancelled»).

Каждый шаг создаёт уведомление второй стороне. Тексты уведомлений не
хранятся: при показе они собираются на языке интерфейса читателя.
"""

from datetime import datetime, timedelta, timezone

from database import (
    db,
    Case,
    Consultant,
    ConsultantAccount,
    ConsultationRequest,
    Notification,
    User,
)


SENT = "sent"
ACCEPTED = "accepted"
ANSWERED = "answered"
DECLINED = "declined"
CANCELLED = "cancelled"

OPEN_STATUSES = (SENT, ACCEPTED)

# Пока без реальных денег: при принятии оплата отмечается как проведённая.
# Когда подключат платёжную систему, здесь появится её режим.
PAYMENT_MODE = "demo"

MAX_QUESTION = 4000
MAX_ANSWER = 20000

DEFAULT_RESPONSE_HOURS = 48

# Подписи статусов (русские строки — ключи переводов интерфейса)
STATUS_LABELS = {
    SENT: "Ожидает принятия",
    ACCEPTED: "В работе",
    ANSWERED: "Ответ получен",
    DECLINED: "Отклонён",
    CANCELLED: "Отменён",
}

# Тексты уведомлений: {name} — имя второй стороны
NOTIFICATION_TEXTS = {
    "new_request": "Новый запрос на консультацию от {name}",
    "accepted": "Запрос принят: {name}",
    "declined": "Запрос отклонён: {name}",
    "answered": "Получен ответ на запрос: {name}",
    "cancelled": "Запрос отменён пользователем {name}",
    "consultant_removed": "Консультант больше недоступен, запрос закрыт",
}


class ConsultationError(Exception):

    """Действие недоступно; message — русская строка для перевода."""

    def __init__(self, message):
        self.message = message
        super().__init__(message)


def utcnow():

    return datetime.now(timezone.utc).replace(tzinfo=None)


def limited(value, limit):

    return (value or "").strip()[:limit]


# ---------- Консультант и аккаунт ----------

def consultant_for_user(user_id):

    """Анкета, привязанная к аккаунту (или None)."""

    link = ConsultantAccount.query.filter_by(user_id=user_id).first()

    if link is None:
        return None

    return db.session.get(Consultant, link.consultant_id)


def active_consultant_for_user(user_id):

    """Проверенная администратором анкета аккаунта — только такие получают запросы."""

    consultant = consultant_for_user(user_id)

    return consultant if consultant is not None and consultant.verified else None


def account_for_consultant(consultant_id):

    link = ConsultantAccount.query.filter_by(consultant_id=consultant_id).first()

    return db.session.get(User, link.user_id) if link else None


def link_account(consultant, user):

    """Привязывает анкету к аккаунту; у аккаунта может быть только одна анкета."""

    if ConsultantAccount.query.filter_by(user_id=user.id).first():
        raise ConsultationError("К этому аккаунту уже привязана анкета консультанта")

    if ConsultantAccount.query.filter_by(consultant_id=consultant.id).first():
        raise ConsultationError("Эта анкета уже привязана к другому аккаунту")

    db.session.add(ConsultantAccount(
        consultant_id=consultant.id,
        user_id=user.id,
        created_at=utcnow(),
    ))


def unlink_consultant(consultant_id):

    ConsultantAccount.query.filter_by(consultant_id=consultant_id).delete()


def consultant_name(consultant):

    if consultant is None:
        return ""

    return " ".join(part for part in (consultant.first_name, consultant.last_name) if part).strip()


def response_hours(consultant):

    try:
        hours = int(str(consultant.response_time or "").strip())
    except ValueError:
        return DEFAULT_RESPONSE_HOURS

    return hours if 0 < hours <= 24 * 30 else DEFAULT_RESPONSE_HOURS


# ---------- Уведомления ----------

def notify(user_id, kind, request):

    if user_id is None:
        return

    db.session.add(Notification(
        user_id=user_id,
        kind=kind,
        request_id=request.id,
        created_at=utcnow(),
    ))


def unread_count(user_id):

    return Notification.query.filter_by(user_id=user_id, read_at=None).count()


def recent_notifications(user_id, limit=20):

    return (
        Notification.query
        .filter_by(user_id=user_id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
        .all()
    )


def notification_view(notification, translate):

    """Данные для показа: текст на языке читателя и ссылка на запрос."""

    request = db.session.get(ConsultationRequest, notification.request_id)

    if request is None:
        return None

    if notification.kind in ("new_request", "cancelled"):
        requester = db.session.get(User, request.requester_id)
        name = requester.username if requester else "—"
    else:
        name = consultant_name(db.session.get(Consultant, request.consultant_id)) or "—"

    template = NOTIFICATION_TEXTS.get(notification.kind)

    if template is None:
        return None

    return {
        "id": notification.id,
        "text": translate(template, name=name),
        "unread": notification.read_at is None,
        "created_at": notification.created_at,
        "request_id": request.id,
    }


def mark_read(notification):

    if notification.read_at is None:
        notification.read_at = utcnow()


def mark_all_read(user_id):

    now = utcnow()

    for notification in Notification.query.filter_by(user_id=user_id, read_at=None):
        notification.read_at = now


def mark_request_notifications_read(user_id, request_id):

    """Открыл запрос — уведомления о нём считаются прочитанными."""

    now = utcnow()

    for notification in Notification.query.filter_by(
        user_id=user_id, request_id=request_id, read_at=None
    ):
        notification.read_at = now


# ---------- Запросы ----------

def create_request(requester, consultant, case, question):

    if consultant is None or not consultant.verified:
        raise ConsultationError("Консультант не найден")

    owner = account_for_consultant(consultant.id)

    if owner is None:
        raise ConsultationError("Этот консультант пока не принимает запросы через приложение")

    if owner.id == requester.id:
        raise ConsultationError("Нельзя отправить запрос самому себе")

    if case is None or case.user_id != requester.id:
        raise ConsultationError("Выберите своё исследование")

    duplicate = ConsultationRequest.query.filter(
        ConsultationRequest.requester_id == requester.id,
        ConsultationRequest.consultant_id == consultant.id,
        ConsultationRequest.case_id == case.id,
        ConsultationRequest.status.in_(OPEN_STATUSES),
    ).first()

    if duplicate is not None:
        raise ConsultationError("Это исследование уже отправлено этому консультанту")

    request = ConsultationRequest(
        requester_id=requester.id,
        consultant_id=consultant.id,
        consultant_user_id=owner.id,
        case_id=case.id,
        question=limited(question, MAX_QUESTION),
        price=(consultant.consult_price or "").strip()[:50],
        response_hours=response_hours(consultant),
        status=SENT,
        payment_status="none",
        created_at=utcnow(),
    )

    db.session.add(request)
    db.session.flush()

    notify(owner.id, "new_request", request)

    return request


def accept(request):

    if request.status != SENT:
        raise ConsultationError("Запрос уже обработан")

    now = utcnow()

    request.status = ACCEPTED
    request.accepted_at = now
    request.due_at = now + timedelta(hours=request.response_hours or DEFAULT_RESPONSE_HOURS)

    # Перевод оплаты консультанту. Пока без реальных денег (PAYMENT_MODE = "demo")
    request.payment_status = "paid"

    notify(request.requester_id, "accepted", request)


def decline(request):

    if request.status != SENT:
        raise ConsultationError("Запрос уже обработан")

    request.status = DECLINED
    request.closed_at = utcnow()

    notify(request.requester_id, "declined", request)


def cancel(request):

    if request.status != SENT:
        raise ConsultationError("Принятый запрос отменить нельзя")

    request.status = CANCELLED
    request.closed_at = utcnow()

    notify(request.consultant_user_id, "cancelled", request)


def answer(request, conclusion, comment):

    if request.status != ACCEPTED:
        raise ConsultationError("Ответить можно только на принятый запрос")

    conclusion = limited(conclusion, MAX_ANSWER)
    comment = limited(comment, MAX_ANSWER)

    if not conclusion:
        raise ConsultationError("Напишите заключение")

    request.status = ANSWERED
    request.answer_conclusion = conclusion
    request.answer_comment = comment
    request.answered_at = utcnow()

    notify(request.requester_id, "answered", request)


def close_for_removed_consultant(consultant_id):

    """Анкету удалили: открытые запросы закрываются, пользователи получают уведомление."""

    now = utcnow()

    for request in ConsultationRequest.query.filter(
        ConsultationRequest.consultant_id == consultant_id,
        ConsultationRequest.status.in_(OPEN_STATUSES),
    ):
        request.status = DECLINED
        request.closed_at = now
        notify(request.requester_id, "consultant_removed", request)


def is_overdue(request, now=None):

    if request.status != ACCEPTED or request.due_at is None:
        return False

    return (now or utcnow()) > request.due_at


def sent_by(user_id):

    return (
        ConsultationRequest.query
        .filter_by(requester_id=user_id)
        .order_by(ConsultationRequest.created_at.desc(), ConsultationRequest.id.desc())
        .all()
    )


def received_by(user_id):

    return (
        ConsultationRequest.query
        .filter_by(consultant_user_id=user_id)
        .filter(ConsultationRequest.status != CANCELLED)
        .order_by(ConsultationRequest.created_at.desc(), ConsultationRequest.id.desc())
        .all()
    )


def pending_incoming_count(user_id):

    return ConsultationRequest.query.filter_by(consultant_user_id=user_id, status=SENT).count()


def role_in(request, user_id):

    """Кем пользователь приходится запросу: requester, consultant или None."""

    if request.requester_id == user_id:
        return "requester"

    if request.consultant_user_id == user_id:
        return "consultant"

    return None


def case_for(request):

    return db.session.get(Case, request.case_id) if request.case_id else None
