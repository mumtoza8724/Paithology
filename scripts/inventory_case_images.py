"""
Инвентаризация снимков исследований (только чтение).

Сопоставляет пути из Case.images с файлами в папке загрузок и сообщает:
- отсутствующие и неиспользуемые файлы;
- файлы, на которые ссылаются несколько исследований (в том числе
  исследования разных пользователей);
- пути особых видов: абсолютные Windows-пути, пути в стиле URL, пути за
  пределами папки загрузок, ссылки на папки, символические ссылки,
  служебные подпапки и возможные совпадения после Unicode-нормализации;
- подозрительные имена файлов («jpg», «image.png», «IMG_0001.jpg»), при
  которых разные загрузки могли перезаписать друг друга.

Скрипт ничего не изменяет и не исправляет:
- база открывается только для чтения (mode=ro, PRAGMA query_only);
- база в режиме WAL и база с файлами -wal/-shm/-journal не открываются,
  чтобы SQLite не создавал и не менял служебные файлы (см. --immutable-copy);
- файлы только перечисляются через lstat; с --checksums читаются лишь
  обычные файлы, без перехода по символическим ссылкам.

В отчёт не попадают имена пациентов, клинические сведения и содержимое
снимков: из базы читаются только id, user_id и images. Файлы обозначаются
псевдонимами file-0001, file-0002 …, которые действуют только внутри одного
отчёта и не позволяют восстановить имя файла.

Коды завершения:
    0  проверка выполнена (без --fail-on-issues — даже при найденных проблемах)
    1  с --fail-on-issues: найдены проблемы
    2  ошибка входных данных: база, папка загрузок, аргументы, режим WAL
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


EXIT_OK = 0
EXIT_ISSUES = 1
EXIT_INPUT_ERROR = 2

# Подпапки загрузок, которые не относятся к снимкам исследований
RESERVED_SUBFOLDERS = {"profiles", "documents"}

KNOWN_EXTENSIONS = {"jpg", "jpeg", "png", "tif", "tiff", "svs", "ndpi"}

BROWSER_RENDERABLE = {"jpg", "jpeg", "png"}

WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")

URL_STYLE = re.compile(r"^(?:https?://|/static/)", re.IGNORECASE)

GENERIC_NAME = re.compile(
    r"^(?:img|image|dsc|dscn|dcim|pxl|mvimg|photo|pic|picture|scan|slide|"
    r"screenshot|untitled|file|download|snimok|foto)[ _\-]?\d*(?:\.\w+)?$",
    re.IGNORECASE
)

DIGITS_ONLY = re.compile(r"^\d+(?:\.\w+)?$")

ISSUE_TITLES = {
    "missing": "Отсутствующие файлы (ссылка есть, файла нет)",
    "outside_uploads": "Пути за пределами папки загрузок",
    "windows_absolute": "Абсолютные Windows-пути (не проверялись)",
    "url_style": "Пути в стиле URL (не проверялись)",
    "absolute_path": "Абсолютные пути (не проверялись)",
    "windows_separators": "Относительные пути с обратными слэшами",
    "directory": "Ссылки на папки",
    "symlink": "Ссылки через символические ссылки (не открывались)",
    "special": "Ссылки на специальные файлы (FIFO, сокеты, устройства)",
    "reserved_subfolder": "Ссылки на служебные подпапки (profiles, documents)",
    "unicode_candidates": "Возможные совпадения после Unicode-нормализации",
    "duplicated_within_case": "Повтор одного файла внутри исследования",
    "shared": "Файлы, общие для нескольких исследований",
    "suspicious_names": "Подозрительные имена файлов (проверить вручную)",
    "unused": "Неиспользуемые файлы",
    "special_files_on_disk": "Специальные файлы в папке загрузок (не открывались)",
    "unreadable": "Недоступные файлы и папки",
}

SUSPICIOUS_REASONS = {
    "only_extension": "имя состоит только из расширения",
    "generic_name": "типовое имя (камера, «image», «scan» и т. п.)",
    "digits_only": "имя только из цифр",
}

TEXT_LIST_LIMIT = 50


class InputError(Exception):
    pass


# ---------- База данных ----------

def sqlite_uri(db_path, immutable):

    # as_uri кодирует «?», «#», пробелы и Unicode, поэтому параметры
    # не теряются и SQLite открывает именно этот файл
    uri = Path(os.path.abspath(db_path)).as_uri() + "?mode=ro"

    if immutable:
        uri += "&immutable=1"

    return uri


def check_database_file(db_path, immutable_copy):

    try:
        info = os.stat(db_path)
    except FileNotFoundError:
        raise InputError(f"База не найдена: {db_path}")
    except OSError as e:
        raise InputError(f"Нет доступа к базе {db_path}: {e.strerror}")

    if not stat.S_ISREG(info.st_mode):
        raise InputError(f"Путь к базе не является обычным файлом: {db_path}")

    if info.st_size == 0:
        raise InputError(f"База пустая (0 байт): {db_path}")

    try:
        with open(db_path, "rb") as f:
            header = f.read(100)
    except OSError as e:
        raise InputError(f"Не удалось прочитать базу {db_path}: {e.strerror}")

    if len(header) < 100 or not header.startswith(b"SQLite format 3\x00"):
        raise InputError(f"Файл не является базой SQLite или повреждён: {db_path}")

    sidecars = [
        suffix for suffix in ("-wal", "-shm", "-journal")
        if os.path.lexists(db_path + suffix)
    ]

    if sidecars:
        raise InputError(
            "Рядом с базой есть служебные файлы SQLite ("
            + ", ".join(sidecars)
            + "). В них могут быть данные, которых нет в основном файле, "
            "а открытие такой базы может изменить служебные файлы. "
            "Сделайте согласованную копию базы (sqlite3 backup API) и "
            "запустите проверку на копии."
        )

    # Байты 18 и 19 заголовка: 1 — обычный журнал, 2 — WAL
    journal = "wal" if header[18] == 2 or header[19] == 2 else "rollback"

    if journal == "wal" and not immutable_copy:
        raise InputError(
            "База в режиме WAL. Даже при чтении SQLite создаёт для неё файлы "
            "-wal и -shm. Сделайте согласованную копию базы и запустите "
            "проверку на копии с флагом --immutable-copy."
        )

    return journal


def read_cases(db_path, immutable_copy):

    journal = check_database_file(db_path, immutable_copy)

    try:
        connection = sqlite3.connect(
            sqlite_uri(db_path, immutable_copy),
            uri=True,
            timeout=2
        )
    except sqlite3.Error as e:
        raise InputError(f"Не удалось открыть базу: {e}")

    # Байты, которые нельзя декодировать как UTF-8, не приводят к падению
    connection.text_factory = lambda b: b.decode("utf-8", "surrogateescape")

    warnings = []

    try:
        connection.execute("PRAGMA query_only = ON")

        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

        if "case" not in tables:
            raise InputError("В базе нет таблицы \"case\" — это не база Paithology?")

        columns = {
            row[1] for row in connection.execute('PRAGMA table_info("case")')
        }

        if not {"id", "user_id", "images"} <= columns:
            raise InputError(
                "В таблице \"case\" нет нужных столбцов (id, user_id, images)"
            )

        # Читаются только технические поля, без данных пациента
        rows = connection.execute(
            'SELECT id, user_id, images FROM "case" ORDER BY id'
        ).fetchall()

        user_ids = None

        if "user" in tables:
            user_ids = {row[0] for row in connection.execute("SELECT id FROM user")}
        else:
            warnings.append(
                "В базе нет таблицы user: удалённых владельцев определить нельзя"
            )

    except sqlite3.OperationalError as e:
        if "locked" in str(e) or "busy" in str(e):
            raise InputError(
                "База заблокирована другой операцией записи. Повторите позже "
                "или запустите проверку на копии базы."
            )
        raise InputError(f"Ошибка чтения базы: {e}")

    except sqlite3.DatabaseError as e:
        raise InputError(f"База повреждена или не читается: {e}")

    finally:
        connection.close()

    return rows, user_ids, journal, warnings


# ---------- Файлы ----------

def display_name(path):

    return os.fsencode(path).decode("utf-8", "backslashreplace")


def nfc(path):

    return unicodedata.normalize("NFC", path)


def is_inside(path, folder):

    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def scan_uploads(uploads_dir):

    regular = {}
    symlinks = []
    special = []
    unreadable = []
    reserved = Counter()

    def on_error(error):
        unreadable.append(error.filename)

    for current, dirs, files in os.walk(uploads_dir, onerror=on_error, followlinks=False):

        relative_dir = os.path.relpath(current, uploads_dir)
        top = relative_dir.split(os.sep)[0]

        for name in dirs:
            if os.path.islink(os.path.join(current, name)):
                symlinks.append(os.path.join(current, name))

        for name in files:

            full = os.path.join(current, name)

            try:
                info = os.lstat(full)
            except OSError:
                unreadable.append(full)
                continue

            if stat.S_ISLNK(info.st_mode):
                symlinks.append(full)
            elif not stat.S_ISREG(info.st_mode):
                special.append(full)
            elif top in RESERVED_SUBFOLDERS:
                reserved[top] += 1
            else:
                regular[full] = info.st_size

    return regular, symlinks, special, unreadable, reserved


def sha256_regular_file(path):

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_BINARY", 0)

    # O_NOFOLLOW не даёт пройти по символической ссылке, O_NONBLOCK не даёт
    # зависнуть на FIFO; после открытия тип проверяется ещё раз
    fd = os.open(path, flags)

    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None

        digest = hashlib.sha256()

        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)

        return digest.hexdigest()

    finally:
        os.close(fd)


def suspicious_reason(path):

    base = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].strip()

    if not base:
        return None

    stem_and_ext = base.rsplit(".", 1)

    if base.lower() in KNOWN_EXTENSIONS or (
        len(stem_and_ext) == 2 and stem_and_ext[0] == ""
    ):
        return "only_extension"

    if DIGITS_ONLY.match(base):
        return "digits_only"

    if GENERIC_NAME.match(base):
        return "generic_name"

    return None


def file_extension(path):

    base = path.replace("\\", "/").rsplit("/", 1)[-1]

    if "." in base.strip("."):
        return display_name(base.rsplit(".", 1)[-1].lower())

    if base.lower() in KNOWN_EXTENSIONS:
        return base.lower()

    return "(нет)"


def classify_reference(raw, project_root, uploads_dir):

    """Возвращает (категория, путь внутри папки загрузок или None)."""

    value = raw.strip()

    if WINDOWS_ABSOLUTE.match(value):
        return "windows_absolute", None

    if URL_STYLE.match(value):
        return "url_style", None

    if value.startswith("/"):
        return "absolute_path", None

    candidate = os.path.normpath(
        os.path.join(project_root, value.replace("\\", "/"))
    )

    if candidate == uploads_dir:
        return "directory", candidate

    if not is_inside(candidate, uploads_dir):
        return "outside_uploads", None

    top = os.path.relpath(candidate, uploads_dir).split(os.sep)[0]

    # «Файла нет» и «нет доступа к файлу или папке» — разные случаи
    try:
        info = os.lstat(candidate)
    except (FileNotFoundError, NotADirectoryError):
        return "missing", candidate
    except OSError:
        return "unreadable", candidate

    # Символическая ссылка в любом звене пути: не открываем и не считаем безопасной
    if os.path.realpath(candidate) != candidate:
        return "symlink", candidate

    if stat.S_ISDIR(info.st_mode):
        return "directory", candidate

    if not stat.S_ISREG(info.st_mode):
        return "special", candidate

    if top in RESERVED_SUBFOLDERS:
        return "reserved_subfolder", candidate

    return "ok", candidate


# ---------- Отчёт ----------

def build_report(args):

    project_root = os.path.realpath(args.project_root)
    uploads_dir = os.path.realpath(args.uploads)

    rows, user_ids, journal, warnings = read_cases(args.db, args.immutable_copy)

    regular, symlinks_on_disk, special_on_disk, unreadable, reserved = scan_uploads(
        uploads_dir
    )

    pseudonyms = {}

    def label(path):

        if args.show_filenames:
            return display_name(os.path.relpath(path, uploads_dir))

        if path not in pseudonyms:
            pseudonyms[path] = f"file-{len(pseudonyms) + 1:04d}"

        return pseudonyms[path]

    by_nfc = defaultdict(list)

    for path in regular:
        by_nfc[nfc(path)].append(path)

    issues = {key: [] for key in ISSUE_TITLES}
    categories = Counter()
    extensions = Counter()
    references = defaultdict(list)

    stats = {
        "total": len(rows),
        "with_images": 0,
        "without_images": 0,
        "without_owner": 0,
        "owner_deleted": None if user_ids is None else 0,
    }

    total_refs = 0

    for case_id, user_id, images in rows:

        if user_id is None:
            stats["without_owner"] += 1
        elif user_ids is not None and user_id not in user_ids:
            stats["owner_deleted"] += 1

        entries = [e for e in (images or "").split(",") if e.strip()]

        if not entries:
            stats["without_images"] += 1
            continue

        stats["with_images"] += 1

        seen = set()

        for index, raw in enumerate(entries):

            total_refs += 1

            category, path = classify_reference(raw, project_root, uploads_dir)

            categories[category] += 1
            extensions[file_extension(raw.strip())] += 1

            entry = {"case_id": case_id, "index": index}

            if path is not None:
                entry["file"] = label(path)

            reason = suspicious_reason(raw)

            if reason:
                issues["suspicious_names"].append(
                    {**entry, "reason": SUSPICIOUS_REASONS[reason]}
                )

            if "\\" in raw and category not in ("windows_absolute",):
                issues["windows_separators"].append(entry)

            if category == "ok":

                if path in seen:
                    issues["duplicated_within_case"].append(entry)

                seen.add(path)
                references[path].append((case_id, user_id))
                continue

            if category == "missing":

                matches = [p for p in by_nfc.get(nfc(path), []) if p != path]

                if matches:
                    issues["unicode_candidates"].append(
                        {**entry, "possible_match": [label(p) for p in matches]}
                    )

            issues[category].append(entry)

    referenced_size = 0

    for path, refs in sorted(references.items(), key=lambda item: label(item[0])):

        referenced_size += regular.get(path, 0)

        case_ids = sorted({case_id for case_id, _ in refs})

        if len(case_ids) > 1:
            issues["shared"].append({
                "file": label(path),
                "cases": case_ids,
                "distinct_owners": len({owner for _, owner in refs}),
                # Эвристика: поля времени создания нет, поэтому самой поздней
                # загрузкой считается исследование с наибольшим id
                "probable_latest_case_id": max(case_ids),
            })

    for path in sorted(regular):

        if path in references:
            continue

        item = {
            "file": label(path),
            "extension": file_extension(path),
            "size": regular[path],
        }

        issues["unused"].append(item)

        reason = suspicious_reason(path)

        if reason:
            issues["suspicious_names"].append(
                {"case_id": None, "file": item["file"], "reason": SUSPICIOUS_REASONS[reason]}
            )

    for path in special_on_disk:
        issues["special_files_on_disk"].append({"file": label(path)})

    for path in unreadable:
        issues["unreadable"].append({"file": label(path)})

    same_content = []

    if args.checksums:

        by_hash = defaultdict(list)

        for path in sorted(regular):
            try:
                digest = sha256_regular_file(path)
            except OSError:
                issues["unreadable"].append({"file": label(path)})
                continue

            if digest is None:
                issues["special_files_on_disk"].append({"file": label(path)})
                continue

            by_hash[digest].append(label(path))

        same_content = [sorted(names) for names in by_hash.values() if len(names) > 1]

    problems_vs_total = sum(
        categories[c] for c in categories if c not in ("ok",)
    )

    if total_refs and problems_vs_total * 2 >= total_refs:
        warnings.append(
            "Больше половины ссылок не найдено или указывает за пределы папки "
            "загрузок. Проверьте --project-root и --uploads: пути в базе "
            "записаны относительно папки, из которой запускалось приложение."
        )

    issue_count = sum(len(items) for items in issues.values())

    return {
        "report_version": 2,
        "pseudonyms": (
            "Реальные имена файлов" if args.show_filenames else
            "file-NNNN: номера действуют только внутри этого отчёта"
        ),
        "database": {
            "journal_mode": journal,
            "opened_as": "immutable-copy" if args.immutable_copy else "read-only",
        },
        "cases": stats,
        "references": {
            "total": total_refs,
            "unique_files": len(references),
            "by_category": dict(categories),
            "extensions": dict(extensions),
            "not_browser_renderable": sum(
                n for ext, n in extensions.items() if ext not in BROWSER_RENDERABLE
            ),
        },
        "files": {
            "regular": len(regular),
            "referenced_size_bytes": referenced_size,
            "symlinks": len(symlinks_on_disk),
            "special": len(special_on_disk),
            "reserved_subfolders": dict(reserved),
        },
        "issues": issues,
        "same_content_groups": same_content,
        "issue_count": issue_count,
        "warnings": warnings,
    }


def print_text(report):

    c = report["cases"]
    r = report["references"]
    f = report["files"]

    print(f"База: журнал {report['database']['journal_mode']}, "
          f"открыта как {report['database']['opened_as']}")
    print("Исследования")
    print(f"  всего: {c['total']}, со снимками: {c['with_images']}, "
          f"без снимков: {c['without_images']}")
    print(f"  без владельца: {c['without_owner']}, владелец удалён: "
          f"{'неизвестно' if c['owner_deleted'] is None else c['owner_deleted']}")
    print("Ссылки на снимки (Case.images)")
    print(f"  всего: {r['total']}, уникальных найденных файлов: {r['unique_files']}")
    print(f"  по категориям: {r['by_category']}")
    print(f"  расширения: {r['extensions']}")
    print(f"  браузер не покажет в <img> (TIFF/SVS/NDPI и др.): {r['not_browser_renderable']}")
    print("Папка загрузок")
    print(f"  обычных файлов снимков: {f['regular']}, объём найденных: "
          f"{f['referenced_size_bytes']} байт")
    print(f"  символических ссылок: {f['symlinks']}, специальных файлов: {f['special']}")
    print(f"  в служебных подпапках (не снимки): {f['reserved_subfolders']}")

    for key, title in ISSUE_TITLES.items():

        items = report["issues"][key]

        if not items:
            continue

        print(f"{title}: {len(items)}")

        for item in items[:TEXT_LIST_LIMIT]:
            print("  " + ", ".join(f"{k}={v}" for k, v in item.items()))

        if len(items) > TEXT_LIST_LIMIT:
            print(f"  … и ещё {len(items) - TEXT_LIST_LIMIT} (полный список: --json)")

    if report["same_content_groups"]:
        print(f"Группы файлов с одинаковым содержимым: {len(report['same_content_groups'])}")
        for group in report["same_content_groups"][:TEXT_LIST_LIMIT]:
            print(f"  {group}")

    print(f"Всего проблем: {report['issue_count']}")
    print(f"Обозначения файлов: {report['pseudonyms']}")


def parse_args(argv):

    parser = argparse.ArgumentParser(
        description="Инвентаризация снимков исследований Paithology (только чтение).",
        epilog="Коды завершения: 0 — проверка выполнена; 1 — найдены проблемы "
               "(только с --fail-on-issues); 2 — ошибка входных данных."
    )
    parser.add_argument(
        "--project-root",
        help="папка, относительно которой записаны пути в Case.images "
             "(обычно корень проекта, откуда запускается app.py). Обязательна, "
             "если в текущей папке нет app.py"
    )
    parser.add_argument(
        "--db",
        help="путь к копии базы SQLite (по умолчанию <project-root>/instance/paithology.db)"
    )
    parser.add_argument(
        "--uploads",
        help="папка загрузок (по умолчанию <project-root>/static/uploads)"
    )
    parser.add_argument(
        "--checksums",
        action="store_true",
        help="посчитать SHA-256 обычных файлов, чтобы найти одинаковые по "
             "содержимому (символические ссылки и специальные файлы не открываются)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="вывести отчёт в JSON"
    )
    parser.add_argument(
        "--fail-on-issues",
        action="store_true",
        help="вернуть код 1, если найдена хотя бы одна проблема"
    )
    parser.add_argument(
        "--immutable-copy",
        action="store_true",
        help="открыть базу с immutable=1. Только для КОПИИ базы, которую никто "
             "не изменяет во время проверки: SQLite не ставит блокировки и не "
             "замечает изменений файла. Нужен для копий в режиме WAL; при наличии "
             "файлов -wal/-shm/-journal проверка всё равно не запускается"
    )
    parser.add_argument(
        "--show-filenames",
        action="store_true",
        help="показать реальные имена файлов вместо псевдонимов. Имена могут "
             "содержать персональные данные — не передавайте такой отчёт"
    )

    return parser.parse_args(argv)


def main(argv=None):

    args = parse_args(argv)

    if args.project_root is None:

        if not os.path.isfile("app.py"):
            print(
                "Ошибка: текущая папка не похожа на корень проекта (нет app.py). "
                "Укажите --project-root — папку, из которой запускается "
                "приложение, иначе пути из базы будут проверены неверно.",
                file=sys.stderr
            )
            return EXIT_INPUT_ERROR

        args.project_root = "."

    if not os.path.isdir(args.project_root):
        print(f"Ошибка: --project-root не является папкой: {args.project_root}",
              file=sys.stderr)
        return EXIT_INPUT_ERROR

    args.db = args.db or os.path.join(args.project_root, "instance", "paithology.db")
    args.uploads = args.uploads or os.path.join(args.project_root, "static", "uploads")

    if not os.path.isdir(args.uploads):
        kind = "не найдена" if not os.path.lexists(args.uploads) else "не является папкой"
        print(f"Ошибка: папка загрузок {kind}: {args.uploads}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    if not os.access(args.uploads, os.R_OK | os.X_OK):
        print(f"Ошибка: нет доступа к папке загрузок: {args.uploads}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    if args.show_filenames:
        print("Внимание: в отчёте будут реальные имена файлов.", file=sys.stderr)

    try:
        report = build_report(args)
    except InputError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    for warning in report["warnings"]:
        print(f"Предупреждение: {warning}", file=sys.stderr)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text(report)

    if args.fail_on_issues and report["issue_count"]:
        return EXIT_ISSUES

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
