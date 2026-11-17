"""
Патогистологические протоколы: временный генератор текстов, справочник
МКБ-О, проверка изображений и сборка документа в DOCX и PDF.

Генерация текстов здесь временная: макроописание собирается по шаблону,
микроописание берётся из шаблонов приложения для 71 диагноза, код МКБ-О —
из справочника. Позже эти функции заменит обученная языковая модель;
интерфейс функций (generate_macro_small, generate_macro_large,
generate_micro) рассчитан на такую замену.

Протоколы не сохраняются на сервере: документ собирается в памяти и сразу
отдаётся пользователю.
"""

import io
import os
import re
from datetime import datetime
from xml.sax.saxutils import escape

from PIL import Image, ImageOps

import icdo


KINDS = ("small", "large")

DOC_LANGS = ("ru", "en")

SOURCE_LANGS = {
    "uz": "Узбекский (кириллица)",
    "ru": "Русский",
    "en": "English",
}

TARGET_LANGS = {
    "ru": "Русский",
    "en": "English",
}

STAIN_METHODS = {
    "Гематоксилин-эозин": "Hematoxylin and eosin",
    "Ван Гизон": "Van Gieson",
    "Папаниколау": "Papanicolaou",
    "Романовский-Гимзе": "Romanowsky-Giemsa",
}

# Цвет и консистенция в родительном падеже, как в шаблоне описания
COLORS = {
    "белесоватого": "whitish",
    "беловато-серого": "whitish-grey",
    "серого": "grey",
    "серо-розового": "greyish-pink",
    "розового": "pink",
    "желтоватого": "yellowish",
    "бурого": "brownish",
    "коричневого": "brown",
    "тёмно-красного": "dark red",
}

CONSISTENCIES = {
    "мягкой": "soft",
    "плотной": "firm",
    "плотноэластической": "firm-elastic",
    "эластической": "elastic",
    "дряблой": "flaccid",
}

WORK_TAKEN = {
    "полностью": "entirely",
    "частично": "partially",
}

ARCHIVE = {
    "без архива": "no archive retained",
    "имеется архив": "archive retained",
}

# Диагнозы, для которых в приложении есть шаблоны микроописаний (model.py)
DIAGNOSES = [
    "Болезнь Крона", "Аденокарцинома двенадцатиперстной кишки",
    "Аденокарцинома сигмовидной кишки", "Аденоматозный полип толстой кишки",
    "Аденоматозный полип желудка", "Аденомиоз матки",
    "Атипическая железистая гиперплазия эндометрия",
    "Пограничная папиллярная цистаденома яичника", "Опухоль Бреннера",
    "Железы цервикального канала с гиперплазией", "Хронический цервицит",
    "Хронический колит", "Хронический эндометрит",
    "Хронический эрозивный проктит", "Хроническая язва желудка",
    "Хронический гастрит", "Хроническая язва кишечника", "Хронический мастит",
    "Хронический проктит", "Хронический сальпингит",
    "Хронический язвенный колит", "CIN I шейки матки", "CIN II шейки матки",
    "CIN III шейки матки", "Аденокарцинома толстой кишки",
    "Дермоидная киста яичника", "Протоковый рак", "Протоковый инвазивный рак",
    "Эндоцервикоз шейки матки", "Аденокарцинома эндометрия",
    "Эндометриоидная аденокарцинома яичника", "Эндометриоидная киста яичника",
    "Эндометриоз маточной трубы", "Эрозивный колит", "Эрозивный проктит",
    "Эрозивно-язвенный колит", "Фиброаденома", "Фиброзно-кистозная мастопатия",
    "Фолликулярная киста яичника", "Аденокарцинома желудка",
    "Железистая гиперплазия эндометрия", "Железистый полип толстой кишки",
    "Железистый полип эндометрия", "Железистый полип желудка", "Гинекомастия",
    "Геморрагическая киста яичника", "Гидросальпинкс", "MALT-лимфома кишечника",
    "MALT-лимфома желудка", "Муцинозная аденокарцинома яичника",
    "Муцинозная аденокарцинома желудка", "Муцинозная цистаденома яичника",
    "Муцинозная аденокарцинома кишечника", "Муцинозная киста яичника",
    "Наботова киста шейки матки", "Неспецифический язвенный колит",
    "Аденокарцинома яичника", "Папиллярная аденокарцинома яичника",
    "Папиллярная цистаденома яичника", "Филлоидная фиброаденома",
    "Пролиферативная мастопатия", "Гнойный мастит", "Аденокарцинома прямой кишки",
    "Склерокистоз яичника", "Серозная киста маточной трубы",
    "Серозная цистаденома яичника", "Серозная киста яичника",
    "Плоскоклеточный рак шейки матки", "Фибромиома матки", "Лейомиома матки",
    "Ворсинчатый полип толстой кишки",
]

# Морфологические коды МКБ-О-3. Это предложение для врача: код всегда
# можно исправить. Для неопухолевых процессов кода МКБ-О нет.
ICDO_CODES = {
    "протоковый рак": "8500/3",
    "протоковый инвазивный рак": "8500/3",
    "аденокарцинома двенадцатиперстной кишки": "8140/3",
    "аденокарцинома сигмовидной кишки": "8140/3",
    "аденокарцинома толстой кишки": "8140/3",
    "аденокарцинома прямой кишки": "8140/3",
    "аденокарцинома желудка": "8140/3",
    "аденокарцинома эндометрия": "8140/3",
    "аденокарцинома яичника": "8140/3",
    "муцинозная аденокарцинома яичника": "8480/3",
    "муцинозная аденокарцинома желудка": "8480/3",
    "муцинозная аденокарцинома кишечника": "8480/3",
    "эндометриоидная аденокарцинома яичника": "8380/3",
    "папиллярная аденокарцинома яичника": "8260/3",
    "плоскоклеточный рак шейки матки": "8070/3",
    "malt-лимфома кишечника": "9699/3",
    "malt-лимфома желудка": "9699/3",
    "cin iii шейки матки": "8077/2",
    "атипическая железистая гиперплазия эндометрия": "8380/2",
    "аденоматозный полип толстой кишки": "8210/0",
    "аденоматозный полип желудка": "8210/0",
    "ворсинчатый полип толстой кишки": "8261/0",
    "фиброаденома": "9010/0",
    "филлоидная фиброаденома": "9020/1",
    "пограничная папиллярная цистаденома яичника": "8451/1",
    "опухоль бреннера": "9000/0",
    "дермоидная киста яичника": "9084/0",
    "муцинозная цистаденома яичника": "8470/0",
    "серозная цистаденома яичника": "8441/0",
    "папиллярная цистаденома яичника": "8450/0",
    "лейомиома матки": "8890/0",
    "фибромиома матки": "8890/0",
}


NOTE_RU = (
    "Примечание: окончательная постановка диагноза осуществляется лечащим "
    "врачом на основании клинических, лабораторных и инструментальных "
    "исследований, в связи с чем гистологические и цитологические заключения "
    "не являются окончательным диагнозом."
)

NOTE_EN = (
    "Note: the final diagnosis is established by the attending physician on "
    "the basis of clinical, laboratory and instrumental investigations; "
    "therefore, histological and cytological conclusions do not constitute "
    "a final diagnosis."
)

LABELS = {
    "ru": {
        "title": "ПАТОГИСТОЛОГИЧЕСКОЕ ИССЛЕДОВАНИЕ",
        "admission_date": "Дата поступления материала:",
        "medical_center": "Название медицинского учреждения",
        "patient_name": "Пациент:",
        "gender": "Пол:",
        "birth_date": "Дата рождения:",
        "operation_type": "Вид операции:",
        "marking": "Маркировка материала, кол-во объектов:",
        "clinical": "Клинический диагноз:",
        "doctor": "Лечащий врач:",
        "amount": "Количество материала:",
        "stain_method": "Методы окраски:",
        "lab_worker": "Лаборант:",
        "result_date": "Дата выдачи результата:",
        "macro": "Макроскопическое описание:",
        "marking_text": "Маркировка:",
        "micro": "Микроскопическое описание:",
        "micro_picture": "Микроскопическая картина:",
        "conclusion": "Патогистологическое заключение:",
        "icdo": "МКБ-О код",
        "note": NOTE_RU,
        "review": "Пересмотр готового среза № {number}",
        "male": "Муж",
        "female": "Жен",
        "date_suffix": " г.",
    },
    "en": {
        "title": "HISTOPATHOLOGICAL EXAMINATION",
        "admission_date": "Date of specimen receipt:",
        "medical_center": "Referring institution",
        "patient_name": "Patient:",
        "gender": "Sex:",
        "birth_date": "Date of birth:",
        "operation_type": "Procedure:",
        "marking": "Specimen labeling, number of items:",
        "clinical": "Clinical diagnosis:",
        "doctor": "Attending physician:",
        "amount": "Amount of material:",
        "stain_method": "Staining methods:",
        "lab_worker": "Laboratory technician:",
        "result_date": "Date of report:",
        "macro": "Gross description:",
        "marking_text": "Marking:",
        "micro": "Microscopic description:",
        "micro_picture": "Microscopic image:",
        "conclusion": "Histopathological conclusion:",
        "icdo": "ICD-O code",
        "note": NOTE_EN,
        "review": "Review of submitted slides No. {number}",
        "male": "Male",
        "female": "Female",
        "date_suffix": "",
    },
}

# Поля, по которым восстанавливается форма при редактировании из архива
MACRO_INPUTS = ("marking_notes", "clinical_lang")

MACRO_BLOCK_FIELDS = ("lang", "size", "color", "consistency", "work", "archive", "notes", "text")

MAX_MACRO_BLOCKS = 10

MAX_CONCLUSIONS = 20
MAX_TEXT = 10000
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_IMAGE_PIXELS = 60_000_000
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "TIFF", "BMP", "MPO"}

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


class ProtocolError(ValueError):
    """Ошибка в данных протокола. message — русская строка-шаблон для перевода
    интерфейса, values — подставляемые в неё значения."""

    def __init__(self, message, **values):
        self.message = message
        self.values = values
        super().__init__(message.format(**values) if values else message)


# ---------- Временный генератор текстов ----------

def to_genitive_adjective(value, feminine=False):

    """«белесоватый» → «белесоватого», «мягкая» → «мягкой»; иначе как есть."""

    word = value.strip().lower()

    if feminine:
        for ending, replacement in (("ая", "ой"), ("яя", "ей")):
            if word.endswith(ending):
                return word[: -len(ending)] + replacement
        return word

    if word.endswith(("ый", "ой")):
        return word[:-2] + "ого"

    if word.endswith("ий"):
        stem = word[:-2]
        return stem + ("ого" if stem.endswith(("к", "г", "х")) else "его")

    return word


def notice_translation(source, target):

    return (
        f"Перевод ({SOURCE_LANGS.get(source, source)} → "
        f"{TARGET_LANGS.get(target, target)}) будет выполнять нейросеть, "
        "она подключается позже. Сейчас текст оформлен без перевода — "
        "проверьте и отредактируйте его."
    )


def generate_macro_small(size, color, consistency, work, archive, lang="ru"):

    """Макроописание биопсии; незаполненные размер, цвет или консистенция пропускаются."""

    size = clean_line(size)
    color_ru = to_genitive_adjective(color) if clean_line(color) else ""
    consistency_ru = to_genitive_adjective(consistency, feminine=True) if clean_line(consistency) else ""
    work = work if work in WORK_TAKEN else "полностью"
    archive = archive if archive in ARCHIVE else "без архива"

    if lang == "en":

        unknown = [
            v for v, table in ((color_ru, COLORS), (consistency_ru, CONSISTENCIES))
            if v and v not in table
        ]

        details = [
            part for part in (
                f"measuring {size}" if size else "",
                COLORS.get(color_ru, color_ru),
                f"of {CONSISTENCIES.get(consistency_ru, consistency_ru)} consistency" if consistency_ru else "",
            ) if part
        ]

        text = (
            "Biopsy material fixed in formalin"
            + (": tissue " + ", ".join(details) if details else "")
            + f". Submitted {WORK_TAKEN[work]}, {ARCHIVE[archive]}."
        )

        notice = (
            "Часть значений не найдена в словаре и оставлена без перевода: "
            + ", ".join(unknown) if unknown else ""
        )

        return text, notice

    details = [
        part for part in (
            f"размерами {size}" if size else "",
            f"{color_ru} цвета" if color_ru else "",
            f"{consistency_ru} консистенции" if consistency_ru else "",
        ) if part
    ]

    text = (
        "Биопсийный материал, фиксированный формалином"
        + (": ткань " + ", ".join(details) if details else "")
        + f". Взят в работу {work}, {archive}."
    )

    return text, ""


def clean_line(text):

    return re.sub(r"\s+", " ", (text or "")).strip()


def sentence(text):

    text = clean_line(text)
    text = re.sub(r"^\(?\d+[.)]\s*", "", text)

    if not text:
        return ""

    text = text[0].upper() + text[1:]

    if text[-1] not in ".!?:":
        text += "."

    return text


def note_lines(notes):

    lines = [sentence(line) for line in re.split(r"[\n;]+", notes or "")]

    return [line for line in lines if line]


def generate_macro_large(notes, marking_notes, source="ru", target="ru", start=1, intro=True):

    lines = note_lines(notes)

    heading = (
        "Surgical specimen fixed in formalin:" if target == "en"
        else "Операционный материал, фиксированный формалином:"
    )

    numbered = "\n".join(f"{i}. {line}" for i, line in enumerate(lines, start))

    # Во втором и следующих блоках вводная строка не повторяется, нумерация продолжается
    text = heading + "\n" + numbered if intro else numbered

    marking = generate_marking(marking_notes)

    notice = notice_translation(source, target) if source != target and lines else ""

    return text, marking, notice


def generate_marking(marking_notes):

    items = []

    for raw in re.split(r"[\n,;]+", marking_notes or ""):

        raw = clean_line(raw)

        if not raw:
            continue

        match = re.match(r"^(.*?\D)[\s:–—-]+(\d[\d\s,–—-]*)$", raw)

        if match:
            name = match.group(1).strip(" :–—-")
            numbers = re.sub(r"\s*[–—]\s*|\s+", "-", match.group(2).strip())
            items.append(f"{name} – {numbers}")
        else:
            items.append(raw)

    marking = ", ".join(items)

    if marking and marking[-1] != ".":
        marking += "."

    return marking


def generate_macro_block(kind, block, target="ru", start=1, intro=True):

    """
    Одно макроописание (например, для одного органа).

    Биопсия: предложение по размеру, цвету и консистенции и/или наброски.
    Операционный материал: нумерованные объекты из набросков.
    Возвращает (текст, пояснение, количество пронумерованных объектов).
    """

    source = block.get("lang") if block.get("lang") in SOURCE_LANGS else "ru"
    notes = block.get("notes", "")

    if kind == "large":
        text, _, notice = generate_macro_large(notes, "", source, target, start, intro)
        return text, notice, len(note_lines(notes))

    parts, notices = [], []

    if any(block.get(key) for key in ("size", "color", "consistency")):
        quick, quick_notice = generate_macro_small(
            block.get("size", ""), block.get("color", ""), block.get("consistency", ""),
            block.get("work", ""), block.get("archive", ""), target,
        )
        parts.append(quick)
        notices.append(quick_notice)

    lines = note_lines(notes)

    if lines:
        parts.append(" ".join(lines))
        if source != target:
            notices.append(notice_translation(source, target))

    return " ".join(parts), " ".join(n for n in notices if n), 0


def generate_clinical(text, source="ru", target="ru"):

    """Клинический диагноз: оформление строки; перевод и грамматику сделает нейросеть."""

    value = sentence(text)

    if not value:
        return "", ""

    # Стадия по TNM латиницей и заглавными: «т2н2м0» → «T2N2M0»; частые опечатки
    # раскладки («И» вместо N) и буква «О» вместо нуля тоже исправляются
    def tnm(m):
        digits = [g.lower().replace("о", "0").replace("o", "0") for g in m.groups()]
        t_value = "is" if digits[0] == "is" else digits[0].upper()
        return f"T{t_value}N{digits[1].upper()}M{digits[2].upper()}"

    value = re.sub(
        r"\b[тt]\s*(is|[0-4x])\s*[нnи]\s*([0-3xоo])\s*[мm]\s*([01xоo])\b",
        tnm,
        value,
        flags=re.IGNORECASE,
    )

    notice = (
        notice_translation(source, target) if source != target else
        "Текст оформлен (заглавная буква, пробелы, точка). Исправление грамматики "
        "будет выполнять нейросеть, она подключается позже."
    )

    return value, notice


def micro_template(diagnosis):

    """Шаблон микроописания из приложения или None, если его нет."""

    try:
        from model import generate_microdescription
        text = (generate_microdescription(diagnosis, "") or "").strip()
    except Exception:
        # Шаблоны недоступны (например, модель не загрузилась): врач вводит текст сам
        return None

    if not text or "требующие дополнительной оценки" in text:
        return None

    return re.sub(r"\s+\n|\n\s+", "\n", text)


def diagnosis_stems(text):

    """Основы слов для нестрогого сравнения: «канцер», «карцинома» → «рак», без степени G."""

    text = clean_line(text).lower()
    text = re.sub(r"\bg\s*-?\s*[1-4x]\b|\(.*?\)", " ", text)
    text = re.sub(r"\b(канцер\w*|карцином\w*)", "рак", text)

    return {word[:5] for word in re.findall(r"[a-zа-яё0-9]+", text) if len(word) > 1}


def canonical_diagnosis(diagnosis):

    """Название из списка шаблонов: точное совпадение или ближайшее по словам."""

    key = clean_line(diagnosis).lower().rstrip(".")

    for name in DIAGNOSES:
        if name.lower() == key:
            return name

    stems = diagnosis_stems(diagnosis)

    best, best_score = None, 0.0

    for name in DIAGNOSES:
        target = diagnosis_stems(name)
        if not target:
            continue
        # Все слова шаблона должны найтись в формулировке врача
        score = len(stems & target) / len(target)
        if score > best_score or (score == best_score and best and len(target) > len(diagnosis_stems(best))):
            best, best_score = name, score

    return best if best_score == 1.0 else None


def suggest_icdo(diagnosis):

    key = clean_line(diagnosis).lower().rstrip(".")

    if not key:
        return ""

    if key in ICDO_CODES:
        return ICDO_CODES[key]

    # Формулировки не из списка: расширенный справочник по ключевым словам
    return icdo.lookup(key)[0]


def generate_micro(diagnosis, lang="ru"):

    """Возвращает (микроописание, код МКБ-О, пояснение)."""

    code = suggest_icdo(diagnosis)

    if lang == "en":
        return "", code, (
            "Микроописание на английском будет генерировать нейросеть, она "
            "подключается позже. Сейчас введите описание вручную."
        )

    name = canonical_diagnosis(diagnosis)
    text = micro_template(name) if name else None

    if text:
        exact = clean_line(diagnosis).lower().rstrip(".") == name.lower()
        return text, code, "" if exact else (
            f"Описание взято из шаблона «{name}». Проверьте, что оно подходит."
        )

    return "", code, (
        "Для этой формулировки пока нет шаблона микроописания. Выберите "
        "диагноз из списка или введите описание вручную."
    )


# ---------- Изображения ----------

def process_image(data, max_side=1600, keep_alpha=False):

    """Проверяет изображение и возвращает очищенный JPEG (или PNG с прозрачностью)."""

    if not data:
        return None

    if len(data) > MAX_IMAGE_BYTES:
        raise ProtocolError("Изображение больше 15 МБ")

    try:
        image = Image.open(io.BytesIO(data))

        if image.format not in ALLOWED_IMAGE_FORMATS:
            raise ProtocolError("Поддерживаются изображения JPG, PNG, WEBP, TIFF, BMP")

        width, height = image.size

        if width * height > MAX_IMAGE_PIXELS:
            raise ProtocolError("Изображение слишком большое по количеству пикселей")

        image = ImageOps.exif_transpose(image)
        image.thumbnail((max_side, max_side))

        out = io.BytesIO()

        # Метаданные (в том числе EXIF с геолокацией) не переносятся
        if keep_alpha and image.mode in ("RGBA", "LA", "P"):
            image.convert("RGBA").save(out, "PNG")
        else:
            image.convert("RGB").save(out, "JPEG", quality=88)

        return out.getvalue()

    except ProtocolError:
        raise
    except Exception:
        raise ProtocolError("Файл не является изображением или повреждён")


# ---------- Данные формы ----------

def limited(value):

    return (value or "").strip()[:MAX_TEXT]


def format_date(value, lang):

    value = (value or "").strip()

    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return value

    return parsed.strftime("%d.%m.%Y") + LABELS[lang]["date_suffix"]


def plural_ru(number, one, few, many):

    n = abs(number) % 100

    if 11 <= n <= 14:
        return many

    n %= 10

    if n == 1:
        return one

    if 2 <= n <= 4:
        return few

    return many


def material_amount(slides, cassettes, lang):

    parts = []

    for value, ru_forms, en_word in (
        (slides, ("стекло", "стекла", "стекол"), "slide"),
        (cassettes, ("кассета", "кассеты", "кассет"), "cassette"),
    ):

        value = (value or "").strip()

        if not value:
            continue

        if not value.isdigit():
            parts.append(value)
        elif lang == "en":
            parts.append(f"{value} {en_word}{'' if value == '1' else 's'}")
        else:
            parts.append(f"{value} {plural_ru(int(value), *ru_forms)}")

    return "; ".join(parts)


def gender_text(value, lang):

    labels = LABELS[lang]

    return {"Мужской": labels["male"], "Женский": labels["female"]}.get(value, value)


def stain_text(value, lang):

    return STAIN_METHODS.get(value, value) if lang == "en" else value


def read_upload(file_storage):

    if file_storage is None or not file_storage.filename:
        return None

    return file_storage.read(MAX_IMAGE_BYTES + 1)


def parse_form(form, files):

    kind = form.get("kind")

    if kind not in KINDS:
        raise ProtocolError("Неизвестный тип протокола")

    lang = form.get("doc_lang") if form.get("doc_lang") in DOC_LANGS else "ru"

    indexes = sorted(
        int(m.group(1)) for key in form
        if (m := re.match(r"^c-(\d{1,3})-diagnosis$", key))
    )

    if len(indexes) > MAX_CONCLUSIONS:
        raise ProtocolError("Не больше {count} заключений", count=MAX_CONCLUSIONS)

    conclusions = []

    for i in indexes:

        diagnosis = limited(form.get(f"c-{i}-diagnosis"))
        micro = limited(form.get(f"c-{i}-micro"))
        image = process_image(read_upload(files.get(f"c-{i}-photo")))
        existing = limited(form.get(f"c-{i}-photo_existing"))[:100]

        if not diagnosis and not micro and not image and not existing:
            continue

        conclusions.append({
            "diagnosis": diagnosis,
            "icdo": limited(form.get(f"c-{i}-icdo"))[:20],
            "micro": micro,
            "image": image,
            # Уже сохранённое фото при редактировании протокола из архива
            "image_existing": existing,
        })

    fields = {
        name: limited(form.get(name))
        for name in (
            "research_number", "admission_date", "medical_center",
            "patient_name", "gender", "birth_date", "operation_type",
            "marking", "clinical", "doctor", "slides", "cassettes",
            "stain_method", "lab_worker", "result_date",
        )
    }

    protocol_id = form.get("protocol_id", "")

    review = form.get("review") == "1"

    macro_blocks = [] if review else parse_macro_blocks(form)

    if review:
        macro_text = ""
    elif macro_blocks:
        macro_text = "\n\n".join(b["text"] for b in macro_blocks if b["text"])
    else:
        # Протоколы и формы без блоков: одно поле с готовым текстом
        macro_text = limited(form.get("macro_text"))

    return {
        "protocol_id": int(protocol_id) if protocol_id.isdigit() else None,
        "kind": kind,
        "lang": lang,
        "review": review,
        "fields": fields,
        "macro_blocks": macro_blocks,
        # Исходные данные генератора, чтобы восстановить форму при редактировании
        "macro_inputs": {
            name: limited(form.get(name))[:2000]
            for name in MACRO_INPUTS
            if form.get(name)
        },
        "macro_text": macro_text,
        "marking_text": limited(form.get("marking_text")) if kind == "large" and not review else "",
        "macro_photo": (
            process_image(read_upload(files.get("macro_photo")))
            if kind == "large" else None
        ),
        "macro_photo_existing": (
            limited(form.get("macro_photo_existing"))[:100] if kind == "large" else ""
        ),
        "conclusions": conclusions,
        "consultant_title": limited(form.get("consultant_title")),
        "consultant_name": limited(form.get("consultant_name")),
        "header": {
            "org": limited(form.get("header_org")),
            "form": limited(form.get("header_form")),
            "address": limited(form.get("header_address")),
            "logo": process_image(
                read_upload(files.get("header_logo")), max_side=600, keep_alpha=True
            ),
        },
    }


def parse_macro_blocks(form):

    """Блоки макроописания mb-<n>-<поле> в порядке номеров."""

    indexes = sorted({
        int(m.group(1)) for key in form
        if (m := re.match(r"^mb-(\d{1,2})-text$", key))
    })

    if len(indexes) > MAX_MACRO_BLOCKS:
        raise ProtocolError("Не больше {count} макроописаний", count=MAX_MACRO_BLOCKS)

    blocks = []

    for i in indexes:

        block = {name: limited(form.get(f"mb-{i}-{name}"))[:MAX_TEXT] for name in MACRO_BLOCK_FIELDS}

        if any(block[name] for name in ("size", "color", "consistency", "notes", "text")):
            blocks.append(block)

    return blocks


def table_rows(data):

    """Строки таблицы первой страницы в порядке образца."""

    f = data["fields"]
    lang = data["lang"]
    labels = LABELS[lang]

    return [
        (labels["admission_date"], format_date(f["admission_date"], lang)),
        (labels["medical_center"], f["medical_center"]),
        (labels["patient_name"], f["patient_name"]),
        (labels["gender"], gender_text(f["gender"], lang)),
        (labels["birth_date"], format_date(f["birth_date"], lang)),
        (labels["operation_type"], f["operation_type"] or "---"),
        (labels["marking"], f["marking"]),
        (labels["clinical"], f["clinical"] or "---"),
        (labels["doctor"], f["doctor"] or "---"),
        (labels["amount"], material_amount(f["slides"], f["cassettes"], lang)),
        (labels["stain_method"], stain_text(f["stain_method"], lang)),
        (labels["lab_worker"], f["lab_worker"]),
        (labels["result_date"], format_date(f["result_date"], lang)),
    ]


def patient_rows(data):

    f = data["fields"]
    lang = data["lang"]
    labels = LABELS[lang]

    return [
        (labels["patient_name"], f["patient_name"]),
        (labels["gender"], gender_text(f["gender"], lang)),
        (labels["birth_date"], format_date(f["birth_date"], lang)),
    ]


def conclusion_line(item, lang):

    text = sentence(item["diagnosis"])

    if item["icdo"]:
        return text, f"{LABELS[lang]['icdo']} {item['icdo']}."

    return text, ""


def macro_lines(text):

    return [line.strip() for line in (text or "").split("\n") if line.strip()]


def consultant_line(data):

    title = data["consultant_title"]
    name = data["consultant_name"]

    if not title and not name:
        return ""

    # Как на бланке лаборатории: должность и ФИО консультанта заглавными
    return f"{title}: ____________ {name}".strip().upper()


def number_line(data):

    number = data["fields"]["research_number"]
    labels = LABELS[data["lang"]]

    if data.get("review"):
        return labels["review"].format(number=number).strip()

    return f"№{number}" if number else ""


def file_name(data, extension):

    """Имя файла «ФИО номер.docx»; символы, запрещённые в именах файлов, заменяются."""

    fields = data["fields"]

    parts = [fields.get("patient_name", ""), fields.get("research_number", "")]
    name = " ".join(p.strip() for p in parts if p and p.strip())

    name = re.sub(r'[\\/]+', "-", name)
    name = re.sub(r'[:*?"<>|\x00-\x1f]+', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .")[:120]

    return f"{name or 'protocol'}.{extension}"


# ---------- DOCX ----------

def build_docx(data):

    from docx import Document
    from docx.enum.section import WD_SECTION
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    lang = data["lang"]
    labels = LABELS[lang]
    header = data["header"]

    doc = Document()

    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(12)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    style.paragraph_format.space_after = Pt(2)

    section = doc.sections[0]
    section.page_height, section.page_width = Cm(29.7), Cm(21.0)
    section.left_margin = section.right_margin = Cm(2)
    section.top_margin = Cm(1.5)
    section.bottom_margin = Cm(1.5)

    def add_field(run, code):
        begin = OxmlElement("w:fldChar")
        begin.set(qn("w:fldCharType"), "begin")
        text = OxmlElement("w:instrText")
        text.set(qn("xml:space"), "preserve")
        text.text = code
        end = OxmlElement("w:fldChar")
        end.set(qn("w:fldCharType"), "end")
        run._r.append(begin)
        run._r.append(text)
        run._r.append(end)

    def picture(paragraph, data_bytes, width_cm):
        paragraph.add_run().add_picture(io.BytesIO(data_bytes), width=Cm(width_cm))

    def text_paragraph(container, text, bold=False, size=None, align=None, justify=False):
        p = container.add_paragraph()
        for i, line in enumerate(text.split("\n")):
            run = p.add_run(line)
            run.bold = bold
            if size:
                run.font.size = Pt(size)
            if i < len(text.split("\n")) - 1:
                run.add_break()
        if align:
            p.alignment = align
        elif justify:
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        return p

    def grid_table(rows, widths=(8.0, 9.0)):
        table = doc.add_table(rows=0, cols=2)
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        for title, value in rows:
            cells = table.add_row().cells
            cells[0].text = title
            cells[1].text = value or ""
            cells[0].width, cells[1].width = Cm(widths[0]), Cm(widths[1])
        return table

    # Колонтитул: логотип и адрес лаборатории на каждой странице
    if header["logo"] or header["address"]:
        top = section.header.add_table(rows=1, cols=2, width=Cm(17))
        left, right = top.rows[0].cells
        if header["logo"]:
            picture(left.paragraphs[0], header["logo"], 4.5)
        if header["address"]:
            right.paragraphs[0].text = ""
            for i, line in enumerate(header["address"].split("\n")):
                run = right.paragraphs[0].add_run(line)
                run.bold = True
                run.font.size = Pt(9)
                if i < len(header["address"].split("\n")) - 1:
                    run.add_break()
            right.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT

    def page_number(paragraph):
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.add_run("PAGE ")
        add_field(paragraph.add_run(), "PAGE")
        paragraph.add_run("/")
        add_field(paragraph.add_run(), "NUMPAGES")

    # Первая страница: внизу только номер страницы
    page_number(section.footer.paragraphs[0])

    # ---- Страница 1 ----
    if header["org"] or header["form"]:
        block = doc.add_table(rows=1, cols=2)
        left, right = block.rows[0].cells
        if header["org"]:
            left.paragraphs[0].text = ""
            for i, line in enumerate(header["org"].split("\n")):
                run = left.paragraphs[0].add_run(line)
                run.bold = True
                if i < len(header["org"].split("\n")) - 1:
                    run.add_break()
        if header["form"]:
            right.paragraphs[0].text = ""
            for i, line in enumerate(header["form"].split("\n")):
                run = right.paragraphs[0].add_run(line)
                run.bold = True
                if i < len(header["form"].split("\n")) - 1:
                    run.add_break()
            right.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
        doc.add_paragraph()

    text_paragraph(doc, labels["title"], bold=True, size=13, align=WD_ALIGN_PARAGRAPH.CENTER)

    if number_line(data):
        text_paragraph(doc, number_line(data), bold=True, size=13, align=WD_ALIGN_PARAGRAPH.CENTER)

    grid_table(table_rows(data))

    # При пересмотре готового среза макроописания нет
    if not data.get("review") and (data["macro_text"] or data["marking_text"]):

        doc.add_paragraph()
        text_paragraph(doc, labels["macro"], bold=True)

        # Каждый пункт отдельным абзацем, нумерованные пункты с отступом, как в образце
        for line in macro_lines(data["macro_text"]):
            p = text_paragraph(doc, line, justify=True)
            if re.match(r"^\d+[.)]", line):
                p.paragraph_format.left_indent = Cm(1)

        if data["marking_text"]:
            p = doc.add_paragraph()
            p.add_run(labels["marking_text"] + " ").bold = True
            p.add_run(data["marking_text"])

    # Вторая страница — отдельный раздел: внизу у неё консультант и примечание
    second = doc.add_section(WD_SECTION.NEW_PAGE)
    second.footer.is_linked_to_previous = False

    footer_paragraphs = second.footer.paragraphs
    line = consultant_line(data)

    if line:
        consultant = footer_paragraphs[0]
        consultant.add_run(line).bold = True
        consultant.paragraph_format.space_after = Pt(10)
        note = second.footer.add_paragraph()
    else:
        note = footer_paragraphs[0]

    note_run = note.add_run(labels["note"])
    note_run.bold = True
    note_run.font.size = Pt(9)
    note.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    page_number(second.footer.add_paragraph())

    # ---- Страница 2 ----
    grid_table(patient_rows(data))
    doc.add_paragraph()

    conclusions = data["conclusions"]

    if data["kind"] == "large":

        photos = [data["macro_photo"]] if data["macro_photo"] else []
        photos += [c["image"] for c in conclusions if c["image"]]

        if photos:
            gallery = doc.add_table(rows=0, cols=2)
            for start in range(0, len(photos), 2):
                cells = gallery.add_row().cells
                for cell, image in zip(cells, photos[start:start + 2]):
                    picture(cell.paragraphs[0], image, 7.5)
                    cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

        micros = [c for c in conclusions if c["micro"]]

        if micros:
            text_paragraph(doc, labels["micro"], bold=True)
            for i, item in enumerate(micros, 1):
                text_paragraph(doc, f"{i}) {item['micro']}", justify=True)

    else:

        table = doc.add_table(rows=1, cols=2)
        table.style = "Table Grid"
        head = table.rows[0].cells
        for cell, title in zip(head, (labels["micro"], labels["micro_picture"])):
            cell.text = ""
            run = cell.paragraphs[0].add_run(title)
            run.bold = True
            cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

        for item in conclusions:
            cells = table.add_row().cells
            cells[0].text = item["micro"]
            if item["image"]:
                picture(cells[1].paragraphs[0], item["image"], 6.5)
                cells[1].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph()

    if conclusions:

        if len(conclusions) == 1 and data["kind"] == "small":
            p = doc.add_paragraph()
            p.add_run(labels["conclusion"] + " ").bold = True
            text, code = conclusion_line(conclusions[0], lang)
            p.add_run(text)
            if code:
                p.add_run(" " + code).bold = True
        else:
            text_paragraph(doc, labels["conclusion"], bold=True)
            for i, item in enumerate(conclusions, 1):
                p = doc.add_paragraph()
                text, code = conclusion_line(item, lang)
                p.add_run(f"{i}. {text}")
                if code:
                    p.add_run(" " + code).bold = True

    out = io.BytesIO()
    doc.save(out)

    return out.getvalue()


# ---------- PDF ----------

def build_pdf(data):

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import (
        BaseDocTemplate, Frame, Image as RLImage, NextPageTemplate, PageBreak,
        PageTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    if "PaithologySerif" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("PaithologySerif", os.path.join(FONT_DIR, "DejaVuSerif.ttf")))
        pdfmetrics.registerFont(TTFont("PaithologySerif-Bold", os.path.join(FONT_DIR, "DejaVuSerif-Bold.ttf")))
        pdfmetrics.registerFontFamily(
            "PaithologySerif", normal="PaithologySerif", bold="PaithologySerif-Bold",
            italic="PaithologySerif", boldItalic="PaithologySerif-Bold"
        )

    lang = data["lang"]
    labels = LABELS[lang]
    header = data["header"]

    base = ParagraphStyle("base", fontName="PaithologySerif", fontSize=10.5, leading=13.5)
    bold = ParagraphStyle("bold", parent=base, fontName="PaithologySerif-Bold")
    justify = ParagraphStyle("justify", parent=base, alignment=TA_JUSTIFY)
    title = ParagraphStyle("title", parent=bold, fontSize=12.5, leading=16, alignment=TA_CENTER)
    right_bold = ParagraphStyle("right_bold", parent=bold, alignment=TA_RIGHT)
    note = ParagraphStyle("note", parent=bold, fontSize=8, leading=10, alignment=TA_JUSTIFY)
    small_right = ParagraphStyle("small_right", parent=bold, fontSize=8, leading=10, alignment=TA_RIGHT)

    def para(text, style=base):
        return Paragraph(escape(text or "").replace("\n", "<br/>"), style)

    def rich(parts, style=base):
        html = "".join(
            f"<b>{escape(t)}</b>" if is_bold else escape(t) for t, is_bold in parts
        )
        return Paragraph(html.replace("\n", "<br/>"), style)

    def image(data_bytes, max_width, max_height=9 * cm):
        reader = ImageReader(io.BytesIO(data_bytes))
        w, h = reader.getSize()
        scale = min(max_width / w, max_height / h)
        return RLImage(io.BytesIO(data_bytes), width=w * scale, height=h * scale)

    grid = TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ])

    plain = TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ])

    width = A4[0] - 4 * cm
    has_top = bool(header["logo"] or header["address"])

    class NumberedCanvas(rl_canvas.Canvas):

        """Холст с итоговым числом страниц для подписи PAGE x/y."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self.setFont("PaithologySerif", 9)
                self.drawCentredString(A4[0] / 2, 1 * cm, f"PAGE {self._pageNumber}/{total}")
                super().showPage()
            super().save()

    def draw_header(canvas, document):
        if not has_top:
            return
        canvas.saveState()
        top = A4[1] - 1.2 * cm
        if header["logo"]:
            reader = ImageReader(io.BytesIO(header["logo"]))
            w, h = reader.getSize()
            scale = min(4.5 * cm / w, 2.2 * cm / h)
            canvas.drawImage(reader, 2 * cm, top - h * scale, w * scale, h * scale, mask="auto")
        if header["address"]:
            block = para(header["address"], small_right)
            _, h = block.wrap(9 * cm, 3 * cm)
            block.drawOn(canvas, A4[0] - 2 * cm - 9 * cm, top - h)
        canvas.restoreState()

    # Низ второй и следующих страниц: консультант заглавными и примечание над номером
    line = consultant_line(data)
    consultant_block = para(line, bold) if line else None
    note_block = para(labels["note"], note)

    _, note_height = note_block.wrap(width, 10 * cm)
    consultant_height = consultant_block.wrap(width, 5 * cm)[1] + 0.35 * cm if consultant_block else 0
    footer_height = note_height + consultant_height

    def draw_later(canvas, document):
        draw_header(canvas, document)
        canvas.saveState()
        note_block.drawOn(canvas, 2 * cm, 1.6 * cm)
        if consultant_block:
            consultant_block.drawOn(canvas, 2 * cm, 1.6 * cm + note_height + 0.35 * cm)
        canvas.restoreState()

    out = io.BytesIO()

    top_margin = (4 if has_top else 1.8) * cm
    first_bottom = 2 * cm
    later_bottom = 1.6 * cm + footer_height + 0.6 * cm

    document = BaseDocTemplate(
        out, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=top_margin, bottomMargin=first_bottom, title=labels["title"],
    )

    document.addPageTemplates([
        PageTemplate(
            id="first",
            frames=[Frame(2 * cm, first_bottom, width, A4[1] - top_margin - first_bottom, id="f1")],
            onPage=draw_header,
        ),
        PageTemplate(
            id="later",
            frames=[Frame(2 * cm, later_bottom, width, A4[1] - top_margin - later_bottom, id="f2")],
            onPage=draw_later,
        ),
    ])

    story = []

    if header["org"] or header["form"]:
        block = Table(
            [[para(header["org"], bold), para(header["form"], right_bold)]],
            colWidths=[width * 0.5, width * 0.5], style=plain
        )
        story += [block, Spacer(1, 0.5 * cm)]

    story.append(para(labels["title"], title))

    if number_line(data):
        story.append(para(number_line(data), title))

    story.append(Spacer(1, 0.3 * cm))
    story.append(Table(
        [[para(a), para(b)] for a, b in table_rows(data)],
        colWidths=[width * 0.47, width * 0.53], style=grid
    ))

    # При пересмотре готового среза макроописания нет
    if not data.get("review") and (data["macro_text"] or data["marking_text"]):

        story += [Spacer(1, 0.5 * cm), para(labels["macro"], bold)]

        indented = ParagraphStyle("indented", parent=justify, leftIndent=1 * cm)

        for line in macro_lines(data["macro_text"]):
            story.append(para(line, indented if re.match(r"^\d+[.)]", line) else justify))

        if data["marking_text"]:
            story += [Spacer(1, 0.3 * cm), rich([(labels["marking_text"] + " ", True), (data["marking_text"], False)])]

    # Со второй страницы — шаблон с консультантом и примечанием внизу
    story += [NextPageTemplate("later"), PageBreak()]
    story.append(Table(
        [[para(a), para(b)] for a, b in patient_rows(data)],
        colWidths=[width * 0.47, width * 0.53], style=grid
    ))
    story.append(Spacer(1, 0.4 * cm))

    conclusions = data["conclusions"]

    if data["kind"] == "large":

        photos = [data["macro_photo"]] if data["macro_photo"] else []
        photos += [c["image"] for c in conclusions if c["image"]]

        if photos:
            rows = [
                [image(p, width * 0.48, 7 * cm) for p in photos[i:i + 2]] + [""] * (2 - len(photos[i:i + 2]))
                for i in range(0, len(photos), 2)
            ]
            story.append(Table(rows, colWidths=[width * 0.5, width * 0.5], style=TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ])))
            story.append(Spacer(1, 0.3 * cm))

        micros = [c for c in conclusions if c["micro"]]

        if micros:
            story.append(para(labels["micro"], bold))
            for i, item in enumerate(micros, 1):
                story.append(para(f"{i}) {item['micro']}", justify))
                story.append(Spacer(1, 0.1 * cm))

    else:

        rows = [[para(labels["micro"], ParagraphStyle("h", parent=bold, alignment=TA_CENTER)),
                 para(labels["micro_picture"], ParagraphStyle("h2", parent=bold, alignment=TA_CENTER))]]
        for item in conclusions:
            rows.append([
                para(item["micro"], base),
                image(item["image"], width * 0.45, 6.5 * cm) if item["image"] else "",
            ])
        style = TableStyle(list(grid.getCommands()) + [("ALIGN", (1, 1), (1, -1), "CENTER"), ("VALIGN", (1, 1), (1, -1), "MIDDLE")])
        story.append(Table(rows, colWidths=[width * 0.5, width * 0.5], style=style))

    story.append(Spacer(1, 0.5 * cm))

    if conclusions:

        if len(conclusions) == 1 and data["kind"] == "small":
            text, code = conclusion_line(conclusions[0], lang)
            story.append(rich([(labels["conclusion"] + " ", True), (text, False), ((" " + code) if code else "", True)]))
        else:
            story.append(para(labels["conclusion"], bold))
            for i, item in enumerate(conclusions, 1):
                text, code = conclusion_line(item, lang)
                story.append(rich([(f"{i}. {text}", False), ((" " + code) if code else "", True)]))

    document.build(story, canvasmaker=NumberedCanvas)

    return out.getvalue()
