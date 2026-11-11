"""
Хранение патогистологических протоколов: архив, история изменений и фото.

Данные протокола хранятся в базе (таблицы Protocol и ProtocolRevision),
фото — в закрытой папке <private_uploads>/protocols/<ключ>/ под случайными
именами. Ключ папки случайный и выдаётся при создании протокола: номер
протокола в SQLite может повториться (после удаления или сброса базы), а ключ —
нет, поэтому новый протокол никогда не окажется в папке со старыми файлами. Фото отдаются только владельцу протокола через маршрут приложения.
"""

import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone

from database import db, Protocol, ProtocolRevision

import protocols


KIND_LABELS = {
    "small": "Малый биопсийный материал",
    "large": "Большой операционный материал",
}

IMAGE_NAME = re.compile(r"^[0-9a-f]{32}\.(?:jpg|png)$")

STORAGE_KEY = re.compile(r"^[0-9a-f]{32}$")

FIELD_TITLES = {
    key: value.rstrip(":")
    for key, value in protocols.LABELS["ru"].items()
    if key in (
        "admission_date", "medical_center", "patient_name", "gender",
        "birth_date", "operation_type", "marking", "clinical", "doctor",
        "stain_method", "lab_worker", "result_date",
    )
}

FIELD_TITLES.update({
    "research_number": "№ исследования",
    "slides": "Количество стекол",
    "cassettes": "Количество кассет",
})


class ProtocolNotFound(Exception):
    pass


def utcnow():

    return datetime.now(timezone.utc).replace(tzinfo=None)


def protocol_folder(base, storage):

    if not storage or not STORAGE_KEY.match(storage):
        raise ProtocolNotFound()

    return os.path.join(base, storage)


def storage_key(protocol):

    return json.loads(protocol.data).get("storage")


def save_image(base, storage, data):

    extension = "png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "jpg"
    name = f"{uuid.uuid4().hex}.{extension}"

    folder = protocol_folder(base, storage)
    os.makedirs(folder, exist_ok=True)

    with open(os.path.join(folder, name), "wb") as f:
        f.write(data)

    return name


def image_names(data):

    """Все имена фото, на которые ссылается снимок данных протокола."""

    if not data:
        return set()

    names = {data.get("macro_photo"), (data.get("header") or {}).get("logo")}
    names.update(c.get("image") for c in data.get("conclusions", []))

    return {name for name in names if name}


def resolve_image(base, storage, new_bytes, existing, allowed):

    if new_bytes:
        return save_image(base, storage, new_bytes)

    if existing and existing in allowed:
        return existing

    return None


def diff(previous, current):

    """Список изменённых разделов для истории."""

    if previous is None:
        return []

    changes = []

    for key, title in FIELD_TITLES.items():
        if previous["fields"].get(key, "") != current["fields"].get(key, ""):
            changes.append(title)

    for key, title in (
        ("lang", "Язык документа"),
        ("macro_text", "Макроскопическое описание"),
        ("marking_text", "Маркировка"),
        ("macro_photo", "Фото макропрепарата"),
    ):
        if previous.get(key) != current.get(key):
            changes.append(title)

    old, new = previous["conclusions"], current["conclusions"]

    for i in range(max(len(old), len(new))):
        if i >= len(old):
            changes.append(f"Заключение {i + 1} добавлено")
        elif i >= len(new):
            changes.append(f"Заключение {i + 1} удалено")
        elif old[i] != new[i]:
            changes.append(f"Заключение {i + 1}")

    if (previous.get("consultant_title"), previous.get("consultant_name")) != (
        current.get("consultant_title"), current.get("consultant_name")
    ):
        changes.append("Подпись")

    if previous.get("header") != current.get("header"):
        changes.append("Шапка лаборатории")

    return changes


def save(user_id, parsed, base):

    """Создаёт или обновляет протокол. Возвращает (протокол, были ли изменения)."""

    now = utcnow()

    if parsed["protocol_id"]:

        protocol = db.session.get(Protocol, parsed["protocol_id"])

        # Чужой протокол неотличим от несуществующего
        if protocol is None or protocol.user_id != user_id:
            raise ProtocolNotFound()

        previous = json.loads(protocol.data)

    else:

        protocol = Protocol(user_id=user_id, kind=parsed["kind"], created_at=now)
        db.session.add(protocol)
        db.session.flush()

        previous = None

    allowed = image_names(previous)
    pid = protocol.id
    storage = previous["storage"] if previous else uuid.uuid4().hex

    header = parsed["header"]
    header_given = any(header.get(k) for k in ("org", "form", "address", "logo"))

    if not header_given and previous:
        # На этом устройстве шапка не настроена: оставляем прежнюю
        stored_header = previous.get("header")
    else:
        stored_header = {
            "org": header["org"],
            "form": header["form"],
            "address": header["address"],
            "logo": save_image(base, storage, header["logo"]) if header["logo"] else None,
        }

    current = {
        "storage": storage,
        "kind": protocol.kind,
        "lang": parsed["lang"],
        "fields": parsed["fields"],
        "macro_inputs": parsed["macro_inputs"],
        "macro_text": parsed["macro_text"],
        "marking_text": parsed["marking_text"],
        "macro_photo": resolve_image(
            base, storage, parsed["macro_photo"], parsed["macro_photo_existing"], allowed
        ),
        "conclusions": [
            {
                "diagnosis": c["diagnosis"],
                "icdo": c["icdo"],
                "micro": c["micro"],
                "image": resolve_image(base, storage, c["image"], c["image_existing"], allowed),
            }
            for c in parsed["conclusions"]
        ],
        "consultant_title": parsed["consultant_title"],
        "consultant_name": parsed["consultant_name"],
        "header": stored_header,
    }

    if previous is not None:

        # Логотип при каждом сохранении записывается заново; одинаковый логотип —
        # не изменение шапки
        if stored_header is not previous.get("header") and stored_header and previous.get("header"):
            if same_file(base, storage, stored_header.get("logo"), previous["header"].get("logo")):
                remove_file(base, storage, stored_header["logo"])
                stored_header["logo"] = previous["header"]["logo"]

        changes = diff(previous, current)

        if not changes:
            db.session.rollback()
            return protocol, False

    else:
        changes = []

    fields = current["fields"]

    protocol.research_number = fields["research_number"][:200]
    protocol.patient_name = fields["patient_name"][:300]
    protocol.admission_date = fields["admission_date"][:20]
    protocol.conclusion_summary = "; ".join(
        c["diagnosis"] for c in current["conclusions"] if c["diagnosis"]
    )[:1000]
    protocol.data = json.dumps(current, ensure_ascii=False)
    protocol.updated_at = now

    db.session.add(ProtocolRevision(
        protocol_id=pid,
        user_id=user_id,
        created_at=now,
        action="Создан" if previous is None else "Изменён",
        changes=json.dumps(changes, ensure_ascii=False),
        data=protocol.data,
    ))

    db.session.commit()

    return protocol, True


def same_file(base, storage, first, second):

    if not first or not second:
        return first == second

    folder = protocol_folder(base, storage)

    try:
        with open(os.path.join(folder, first), "rb") as a, \
                open(os.path.join(folder, second), "rb") as b:
            return a.read() == b.read()
    except OSError:
        return False


def remove_file(base, storage, name):

    if name and IMAGE_NAME.match(name):
        try:
            os.remove(os.path.join(protocol_folder(base, storage), name))
        except OSError:
            pass


def get_own(protocol_id, user_id):

    protocol = db.session.get(Protocol, protocol_id)

    if protocol is None or protocol.user_id != user_id:
        raise ProtocolNotFound()

    return protocol


def read_image(base, storage, name):

    if not name or not IMAGE_NAME.match(name):
        return None

    try:
        with open(os.path.join(protocol_folder(base, storage), name), "rb") as f:
            return f.read()
    except OSError:
        return None


def document_data(protocol, base):

    """Данные для build_docx/build_pdf из сохранённого протокола."""

    data = json.loads(protocol.data)
    header = data.get("header") or {}

    return {
        "kind": data["kind"],
        "lang": data["lang"],
        "fields": data["fields"],
        "macro_text": data["macro_text"],
        "marking_text": data["marking_text"],
        "macro_photo": read_image(base, data["storage"], data.get("macro_photo")),
        "conclusions": [
            {**c, "image": read_image(base, data["storage"], c.get("image"))}
            for c in data["conclusions"]
        ],
        "consultant_title": data["consultant_title"],
        "consultant_name": data["consultant_name"],
        "header": {
            "org": header.get("org", ""),
            "form": header.get("form", ""),
            "address": header.get("address", ""),
            "logo": read_image(base, data["storage"], header.get("logo")),
        },
    }


def history(protocol):

    revisions = (
        ProtocolRevision.query
        .filter_by(protocol_id=protocol.id)
        .order_by(ProtocolRevision.id.desc())
        .all()
    )

    return [
        {
            "created_at": r.created_at,
            "action": r.action,
            "changes": json.loads(r.changes or "[]"),
        }
        for r in revisions
    ]


def search(user_id, research_number="", patient_name="", conclusion="", admission_date=""):

    query = Protocol.query.filter_by(user_id=user_id)

    if research_number:
        query = query.filter(Protocol.research_number.contains(research_number))

    if patient_name:
        query = query.filter(Protocol.patient_name.contains(patient_name))

    if conclusion:
        query = query.filter(Protocol.conclusion_summary.contains(conclusion))

    if admission_date:
        query = query.filter_by(admission_date=admission_date)

    return query.order_by(Protocol.updated_at.desc(), Protocol.id.desc()).all()


def delete(protocol, base):

    folder = protocol_folder(base, storage_key(protocol))

    ProtocolRevision.query.filter_by(protocol_id=protocol.id).delete()
    db.session.delete(protocol)
    db.session.commit()

    # Папка протокола (по случайному ключу) содержит только его собственные фото
    shutil.rmtree(folder, ignore_errors=True)
