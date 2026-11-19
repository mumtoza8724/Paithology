from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Case(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))


    patient_name = db.Column(
        db.String(300)
    )

    birth_date = db.Column(
        db.String(100)
    )

    organ = db.Column(
        db.String(100)
    )

    gender = db.Column(
        db.String(50)
    )

    operation_type = db.Column(
        db.String(100)
    )

    clinical = db.Column(
        db.Text
    )

    description = db.Column(
        db.Text
    )

    conclusion = db.Column(
        db.Text
    )

    images = db.Column(
        db.Text
    )

    research_number = db.Column(db.String(100))
    medical_center = db.Column(db.String(200))
    admission_date = db.Column(db.String(100))
    result_date = db.Column(db.String(100))
    doctor = db.Column(db.String(200))
    slides = db.Column(db.String(50))
    cassettes = db.Column(db.String(50))
    stain_method = db.Column(db.String(200))
    lab_worker = db.Column(db.String(200))
    material_color = db.Column(db.String(100))
    material_size = db.Column(db.String(100))
    material_consistency = db.Column(db.String(100))

class Consultant(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    email = db.Column(db.String(200))

    country = db.Column(db.String(100))
    city = db.Column(db.String(100))

    degree = db.Column(db.String(200))

    organization = db.Column(db.String(200))
    position = db.Column(db.String(200))

    experience = db.Column(db.String(100))

    specializations = db.Column(db.String(500))
    languages = db.Column(db.String(500))

    response_time = db.Column(db.String(100))

    consult_price = db.Column(db.String(100))

    consultant_type = db.Column(db.String(100))

    photo = db.Column(db.String(500))

    card = db.Column(db.String(100))
    owner = db.Column(db.String(200))

    documents = db.Column(db.Text)

    verified = db.Column(
        db.Boolean,
        default=False
    )

class User(db.Model):
            id = db.Column(
                db.Integer,
                primary_key=True
            )

            username = db.Column(
                db.String(100),
                unique=True
            )

            password = db.Column(
                db.String(100)
            )

            free_attempts = db.Column(
                db.Integer,
                default=10
            )

            is_consultant = db.Column(
                db.Boolean,
                default=False
            )

            is_paid = db.Column(
                db.Boolean,
                default=False
            )

class Protocol(db.Model):

    """Патогистологический протокол, созданный на странице «Создать протокол»."""

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)

    kind = db.Column(db.String(10))

    research_number = db.Column(db.String(200))
    patient_name = db.Column(db.String(300))
    admission_date = db.Column(db.String(20))

    # Краткий текст заключений для списка и поиска
    conclusion_summary = db.Column(db.Text)

    # Все данные протокола в JSON (поля, тексты, имена файлов фото)
    data = db.Column(db.Text)

    created_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime)


class ProtocolRevision(db.Model):

    """История изменений протокола: снимок данных после каждого изменения."""

    id = db.Column(db.Integer, primary_key=True)

    protocol_id = db.Column(db.Integer, db.ForeignKey("protocol.id"), index=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    created_at = db.Column(db.DateTime)

    action = db.Column(db.String(50))

    # Список изменённых разделов (JSON)
    changes = db.Column(db.Text)

    data = db.Column(db.Text)


class CaseDetails(db.Model):

    """
    Дополнительные данные исследования с главной страницы (ИИ-анализ),
    которых нет в таблице Case: несколько макроописаний, язык документа и
    язык ввода клинического диагноза. Отдельная таблица, чтобы не менять
    существующую таблицу Case.
    """

    id = db.Column(db.Integer, primary_key=True)

    case_id = db.Column(db.Integer, db.ForeignKey("case.id"), unique=True, index=True)

    data = db.Column(db.Text)


# ---------- Консультации ----------
# Новые таблицы; существующие (Case, Consultant, User) не меняются.


class ConsultantAccount(db.Model):

    """Связь анкеты консультанта с аккаунтом пользователя (одна анкета на аккаунт)."""

    id = db.Column(db.Integer, primary_key=True)

    consultant_id = db.Column(db.Integer, db.ForeignKey("consultant.id"), unique=True, index=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, index=True)

    created_at = db.Column(db.DateTime)


class ConsultationRequest(db.Model):

    """
    Запрос на консультацию: пользователь отправляет своё исследование
    консультанту. Статусы: sent → accepted → answered; а также declined
    (консультант отклонил) и cancelled (пользователь отменил до принятия).
    """

    id = db.Column(db.Integer, primary_key=True)

    requester_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)

    consultant_id = db.Column(db.Integer, db.ForeignKey("consultant.id"), index=True)

    # Аккаунт консультанта на момент отправки: ему приходит запрос
    consultant_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)

    case_id = db.Column(db.Integer, db.ForeignKey("case.id"), index=True)

    question = db.Column(db.Text)

    # Цена и срок ответа фиксируются в момент отправки
    price = db.Column(db.String(50))
    response_hours = db.Column(db.Integer)

    status = db.Column(db.String(20), index=True)

    # Оплата: none → paid при принятии. Пока без реального перевода денег
    payment_status = db.Column(db.String(20))

    created_at = db.Column(db.DateTime)
    accepted_at = db.Column(db.DateTime)
    due_at = db.Column(db.DateTime)
    answered_at = db.Column(db.DateTime)
    closed_at = db.Column(db.DateTime)

    answer_conclusion = db.Column(db.Text)
    answer_comment = db.Column(db.Text)


class Notification(db.Model):

    """Уведомление пользователю; текст собирается при показе на его языке."""

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)

    kind = db.Column(db.String(30))

    request_id = db.Column(db.Integer, db.ForeignKey("consultation_request.id"), index=True)

    created_at = db.Column(db.DateTime)

    read_at = db.Column(db.DateTime)
