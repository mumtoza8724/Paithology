"""
Инвентаризация снимков исследований (только чтение).

Сопоставляет пути из Case.images с файлами в папке загрузок:
- отсутствующие файлы (ссылка есть, файла нет);
- неиспользуемые файлы (файл есть, ссылок нет);
- файлы, на которые ссылаются несколько исследований (в том числе
  исследования разных пользователей);
- ссылки за пределами папки загрузок и нестандартные форматы путей.

Скрипт ничего не изменяет: база открывается в режиме только для чтения,
файлы не открываются на запись, не перемещаются и не удаляются.

В отчёт не попадают имена пациентов, клинические сведения и содержимое
снимков. Имена файлов по умолчанию заменяются коротким хешем, потому что
в имени файла могут быть персональные данные. Выводятся только id
исследований, id пользователей, хеши путей и агрегированные количества.

Запуск (из корня проекта):
    python scripts/inventory_case_images.py \
        --db instance/paithology.db --uploads static/uploads

Параметры:
    --project-root  папка, относительно которой записаны пути в базе
                    (по умолчанию текущая папка)
    --checksums     дополнительно посчитать SHA-256 файлов (только чтение),
                    чтобы найти одинаковые по содержимому файлы
    --json          вывести результат в JSON
    --show-filenames  показать реальные имена файлов вместо хешей
                    (может раскрыть персональные данные, по умолчанию выкл.)
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict


# Подпапки загрузок, которые не относятся к снимкам исследований
NON_CASE_SUBFOLDERS = {"profiles", "documents"}

KNOWN_EXTENSIONS = {"jpg", "jpeg", "png", "tif", "tiff", "svs", "ndpi"}

BROWSER_RENDERABLE = {"jpg", "jpeg", "png"}


def path_id(relative_path):

    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:12]


def file_extension(name):

    base = name.rsplit("/", 1)[-1]

    if "." in base.strip("."):
        return base.rsplit(".", 1)[-1].lower()

    # secure_filename превращает «снимок.jpg» в «jpg»
    if base.lower() in KNOWN_EXTENSIONS:
        return base.lower() + " (имя без основы)"

    return "(нет расширения)"


def path_style(raw):

    if "\\" in raw:
        return "обратные слэши (Windows)"

    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        return "абсолютный путь"

    if raw.startswith("static/uploads/"):
        return "static/uploads/<имя>"

    return "другой относительный путь"


def normalize(raw, project_root):

    value = raw.strip().replace("\\", "/")

    if len(value) > 1 and value[1] == ":":
        return os.path.normpath(value)

    if value.startswith("/"):
        return os.path.normpath(value)

    return os.path.normpath(os.path.join(project_root, value))


def sha256_file(path):

    digest = hashlib.sha256()

    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def read_cases(db_path):

    uri = "file:" + os.path.abspath(db_path) + "?mode=ro"

    connection = sqlite3.connect(uri, uri=True)

    try:
        connection.execute("PRAGMA query_only = ON")

        # Читаются только технические поля, без данных пациента
        rows = connection.execute(
            'SELECT id, user_id, images FROM "case" ORDER BY id'
        ).fetchall()

        user_ids = {
            row[0] for row in connection.execute("SELECT id FROM user")
        }

    finally:
        connection.close()

    return rows, user_ids


def scan_uploads(uploads_dir):

    case_files = {}
    other_files = Counter()
    symlinks = 0

    for current, dirs, files in os.walk(uploads_dir, followlinks=False):

        relative_dir = os.path.relpath(current, uploads_dir)

        top = relative_dir.split(os.sep)[0]

        for name in files:

            full = os.path.join(current, name)

            if os.path.islink(full):
                symlinks += 1
                continue

            if top in NON_CASE_SUBFOLDERS:
                other_files[top] += 1
                continue

            case_files[os.path.realpath(full)] = os.path.getsize(full)

    return case_files, other_files, symlinks


def build_report(args):

    project_root = os.path.realpath(args.project_root)
    uploads_dir = os.path.realpath(args.uploads)

    rows, user_ids = read_cases(args.db)

    files_on_disk, other_files, symlinks = scan_uploads(uploads_dir)

    def label(full_path):

        relative = os.path.relpath(full_path, uploads_dir).replace(os.sep, "/")

        return relative if args.show_filenames else path_id(relative)

    references = defaultdict(list)
    styles = Counter()
    extensions = Counter()
    outside = []
    duplicates_in_case = []
    cases_without_images = 0
    cases_without_owner = 0
    cases_with_missing_owner = 0
    total_refs = 0

    for case_id, user_id, images in rows:

        if user_id is None:
            cases_without_owner += 1
        elif user_id not in user_ids:
            cases_with_missing_owner += 1

        entries = [e for e in (images or "").split(",") if e.strip()]

        if not entries:
            cases_without_images += 1
            continue

        seen = set()

        for index, raw in enumerate(entries):

            total_refs += 1
            styles[path_style(raw.strip())] += 1

            full = os.path.realpath(normalize(raw, project_root))

            extensions[file_extension(full.replace(os.sep, "/"))] += 1

            if os.path.commonpath([full, uploads_dir]) != uploads_dir:
                outside.append({"case_id": case_id, "index": index})
                continue

            if full in seen:
                duplicates_in_case.append({"case_id": case_id, "index": index})

            seen.add(full)

            references[full].append(
                {"case_id": case_id, "user_id": user_id, "index": index}
            )

    missing = []
    shared = []
    referenced_size = 0

    for full, refs in sorted(references.items()):

        if full not in files_on_disk:
            for ref in refs:
                missing.append(
                    {"case_id": ref["case_id"], "index": ref["index"], "file": label(full)}
                )
            continue

        referenced_size += files_on_disk[full]

        case_ids = sorted({r["case_id"] for r in refs})

        if len(case_ids) > 1:
            owners = {r["user_id"] for r in refs}
            shared.append({
                "file": label(full),
                "cases": case_ids,
                "distinct_owners": len(owners)
            })

    unused = [
        {
            "file": label(full),
            "extension": file_extension(full.replace(os.sep, "/")),
            "size": size
        }
        for full, size in sorted(files_on_disk.items())
        if full not in references
    ]

    content_groups = []

    if args.checksums:

        by_hash = defaultdict(list)

        for full in files_on_disk:
            by_hash[sha256_file(full)].append(label(full))

        content_groups = [
            sorted(names) for names in by_hash.values() if len(names) > 1
        ]

    return {
        "cases": {
            "total": len(rows),
            "with_images": len(rows) - cases_without_images,
            "without_images": cases_without_images,
            "without_owner": cases_without_owner,
            "owner_deleted": cases_with_missing_owner,
        },
        "references": {
            "total": total_refs,
            "unique_files": len(references),
            "path_styles": dict(styles),
            "extensions": dict(extensions),
            "not_browser_renderable": sum(
                count for ext, count in extensions.items()
                if ext.split(" ")[0] not in BROWSER_RENDERABLE
            ),
            "outside_uploads": outside,
            "duplicated_within_case": duplicates_in_case,
        },
        "files": {
            "case_files_on_disk": len(files_on_disk),
            "referenced_size_bytes": referenced_size,
            "other_subfolders": dict(other_files),
            "symlinks_skipped": symlinks,
        },
        "missing": missing,
        "shared": shared,
        "shared_between_owners": sum(1 for s in shared if s["distinct_owners"] > 1),
        "unused": unused,
        "unused_size_bytes": sum(u["size"] for u in unused),
        "same_content_groups": content_groups,
        "filenames_hidden": not args.show_filenames,
    }


def print_text(report):

    c = report["cases"]
    r = report["references"]
    f = report["files"]

    print("Исследования")
    print(f"  всего: {c['total']}, со снимками: {c['with_images']}, "
          f"без снимков: {c['without_images']}")
    print(f"  без владельца: {c['without_owner']}, владелец удалён: {c['owner_deleted']}")

    print("Ссылки на снимки (Case.images)")
    print(f"  всего ссылок: {r['total']}, уникальных файлов: {r['unique_files']}")
    print(f"  форматы путей: {r['path_styles']}")
    print(f"  расширения: {r['extensions']}")
    print(f"  браузер не покажет в <img> (TIFF/SVS/NDPI и др.): {r['not_browser_renderable']}")
    print(f"  ссылки за пределами папки загрузок: {len(r['outside_uploads'])}")
    print(f"  повтор одного файла внутри исследования: {len(r['duplicated_within_case'])}")

    print("Файлы в папке загрузок")
    print(f"  файлов снимков: {f['case_files_on_disk']}, "
          f"объём используемых: {f['referenced_size_bytes']} байт")
    print(f"  в служебных подпапках (не снимки): {f['other_subfolders']}")
    print(f"  пропущено символических ссылок: {f['symlinks_skipped']}")

    print(f"Отсутствующие файлы: {len(report['missing'])}")
    for item in report["missing"]:
        print(f"  case_id={item['case_id']} снимок №{item['index']} файл={item['file']}")

    print(f"Файлы, общие для нескольких исследований: {len(report['shared'])} "
          f"(из них у разных пользователей: {report['shared_between_owners']})")
    for item in report["shared"]:
        print(f"  файл={item['file']} case_id={item['cases']} "
              f"владельцев={item['distinct_owners']}")

    print(f"Неиспользуемые файлы: {len(report['unused'])}, "
          f"объём: {report['unused_size_bytes']} байт")
    for item in report["unused"]:
        print(f"  файл={item['file']} расширение={item['extension']} размер={item['size']}")

    if report["same_content_groups"]:
        print(f"Группы файлов с одинаковым содержимым: {len(report['same_content_groups'])}")
        for group in report["same_content_groups"]:
            print(f"  {group}")

    if report["filenames_hidden"]:
        print("Имена файлов скрыты (показаны первые 12 символов SHA-256 пути).")


def main():

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", default=os.path.join("instance", "paithology.db"))
    parser.add_argument("--uploads", default=os.path.join("static", "uploads"))
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--checksums", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-filenames", action="store_true")

    args = parser.parse_args()

    for path, kind in ((args.db, "база"), (args.uploads, "папка загрузок")):
        if not os.path.exists(path):
            print(f"Не найдена {kind}: {path}", file=sys.stderr)
            return 1

    report = build_report(args)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
