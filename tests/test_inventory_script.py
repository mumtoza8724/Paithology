"""
Тесты скрипта scripts/inventory_case_images.py.

Только синтетические данные во временных папках: искусственная SQLite-база
с той же структурой таблиц, что у приложения, и файлы-заглушки по несколько
байт. Скрипт запускается отдельным процессом, как его запускает оператор.
"""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import unicodedata

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = os.path.join(ROOT, "scripts", "inventory_case_images.py")

PHI = {
    "patient_name": "PHI_NAME_Ivanova",
    "clinical": "PHI_CLINICAL_marker",
    "description": "PHI_DESCRIPTION_marker",
    "conclusion": "PHI_DIAGNOSIS_marker",
    "birth_date": "PHI_BIRTH_1999",
}


# ---------- Вспомогательные функции ----------

def make_db(path, cases, users=(1, 2), journal=None):

    connection = sqlite3.connect(path)

    connection.execute(
        "CREATE TABLE user (id INTEGER PRIMARY KEY, username TEXT, password TEXT)"
    )
    connection.execute(
        'CREATE TABLE "case" (id INTEGER PRIMARY KEY, user_id INTEGER, '
        "patient_name TEXT, clinical TEXT, description TEXT, conclusion TEXT, "
        "birth_date TEXT, images TEXT)"
    )

    for user_id in users:
        connection.execute(
            "INSERT INTO user (id, username, password) VALUES (?, ?, ?)",
            (user_id, f"user{user_id}", "x")
        )

    for user_id, images in cases:
        connection.execute(
            'INSERT INTO "case" (user_id, patient_name, clinical, description, '
            "conclusion, birth_date, images) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, PHI["patient_name"], PHI["clinical"], PHI["description"],
             PHI["conclusion"], PHI["birth_date"], images)
        )

    connection.commit()

    if journal:
        connection.execute(f"PRAGMA journal_mode={journal}")

    connection.close()


def make_project(base, cases, files=None, users=(1, 2), journal=None):

    project = base / "project"
    uploads = project / "static" / "uploads"

    for folder in (project / "instance", uploads / "profiles", uploads / "documents"):
        folder.mkdir(parents=True, exist_ok=True)

    # Признак корня проекта для запуска без --project-root
    (project / "app.py").write_text("# заглушка\n")

    for name, data in (files or {}).items():
        target = uploads / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    make_db(str(project / "instance" / "paithology.db"), cases, users, journal)

    return project


def run(args, cwd, timeout=60):

    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")

    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=timeout
    )


def run_json(project, *extra):

    result = run(["--json", *extra], cwd=project)

    assert result.returncode == 0, result.stderr

    return json.loads(result.stdout), result


def snapshot(base):

    """Состояние всех объектов: тип, размер, mtime, права, SHA-256 обычных файлов."""

    state = {}

    for current, dirs, files in os.walk(base, followlinks=False):

        for name in dirs + files:

            path = os.path.join(current, name)
            info = os.lstat(path)
            key = os.fsencode(os.path.relpath(path, base))

            digest = None

            if stat.S_ISREG(info.st_mode):
                with open(path, "rb") as f:
                    digest = hashlib.sha256(f.read()).hexdigest()

            link = os.readlink(path) if stat.S_ISLNK(info.st_mode) else None

            state[key] = (
                stat.S_IFMT(info.st_mode),
                stat.S_IMODE(info.st_mode),
                info.st_size if not stat.S_ISDIR(info.st_mode) else None,
                info.st_mtime_ns if not stat.S_ISDIR(info.st_mode) else None,
                digest,
                link,
            )

    return state


def assert_no_traceback(result):

    assert "Traceback" not in result.stdout + result.stderr


def cases_in(report, key):

    return sorted(item["case_id"] for item in report["issues"][key])


def load_script_module():

    spec = importlib.util.spec_from_file_location("inventory_case_images", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


needs_posix = pytest.mark.skipif(
    os.name != "posix", reason="нужны возможности POSIX (FIFO, права, имена в байтах)"
)


# ---------- Путь к базе ----------

@pytest.mark.parametrize(
    "folder",
    ["dir?query", "dir#hash", "dir with spaces", "папка_ünicode", "a?b#c d_ж"]
)
def test_special_characters_in_db_path(tmp_path, folder):

    if os.name == "nt" and any(c in folder for c in "?#"):
        pytest.skip("Windows не допускает «?» в именах")

    project = make_project(tmp_path, [(1, "static/uploads/a.png")], {"a.png": b"A"})

    weird = tmp_path / folder
    weird.mkdir()

    db_copy = weird / "copy of база.db"
    shutil.copy(project / "instance" / "paithology.db", db_copy)

    before = snapshot(tmp_path)

    report, result = run_json(project, "--db", str(db_copy))

    assert report["cases"]["total"] == 1
    assert report["references"]["by_category"] == {"ok": 1}

    # Ни нового файла рядом (например, «dir» при потере «?…»), ни -wal/-shm
    assert snapshot(tmp_path) == before


def test_uri_is_percent_encoded():

    module = load_script_module()

    uri = module.sqlite_uri("/tmp/a?b#c d/ж.db", immutable=False)

    assert uri.endswith("?mode=ro")
    assert "%3F" in uri and "%23" in uri and "%20" in uri
    assert uri.count("?") == 1 and "#" not in uri


def test_connection_is_read_only(tmp_path):

    project = make_project(tmp_path, [(1, "static/uploads/a.png")], {"a.png": b"A"})

    module = load_script_module()

    db = str(project / "instance" / "paithology.db")

    connection = sqlite3.connect(module.sqlite_uri(db, immutable=False), uri=True)

    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute('UPDATE "case" SET images = \'x\'')
    finally:
        connection.close()


def test_run_does_not_change_anything(tmp_path):

    project = make_project(
        tmp_path,
        [(1, "static/uploads/a.png"), (2, "static/uploads/missing.png")],
        {"a.png": b"A", "unused.png": b"U"}
    )

    before = snapshot(tmp_path)

    for extra in ([], ["--json"], ["--checksums"], ["--json", "--checksums"]):
        result = run(extra, cwd=project)
        assert result.returncode == 0, result.stderr

    assert snapshot(tmp_path) == before

    leftovers = [
        p for p in os.listdir(project / "instance")
        if p.endswith(("-wal", "-shm", "-journal"))
    ]

    assert leftovers == []


# ---------- Режим WAL и служебные файлы ----------

def test_wal_database_is_refused(tmp_path):

    project = make_project(
        tmp_path, [(1, "static/uploads/a.png")], {"a.png": b"A"}, journal="wal"
    )

    before = snapshot(tmp_path)

    result = run([], cwd=project)

    assert result.returncode == 2
    assert "WAL" in result.stderr
    assert_no_traceback(result)
    assert snapshot(tmp_path) == before


def test_wal_copy_with_immutable_flag(tmp_path):

    project = make_project(
        tmp_path, [(1, "static/uploads/a.png")], {"a.png": b"A"}, journal="wal"
    )

    before = snapshot(tmp_path)

    report, _ = run_json(project, "--immutable-copy")

    assert report["database"] == {"journal_mode": "wal", "opened_as": "immutable-copy"}
    assert report["cases"]["total"] == 1
    assert snapshot(tmp_path) == before


def test_live_wal_with_uncheckpointed_data_is_refused(tmp_path):

    project = make_project(tmp_path, [(1, "static/uploads/a.png")], {"a.png": b"A"})

    db = str(project / "instance" / "paithology.db")

    writer = sqlite3.connect(db)

    try:
        writer.execute("PRAGMA journal_mode=wal")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute('INSERT INTO "case" (user_id, images) VALUES (1, \'static/uploads/a.png\')')
        writer.commit()

        assert os.path.exists(db + "-wal")

        # Данные второго исследования есть только в -wal: immutable=1 их бы
        # не увидел, поэтому проверка не должна запускаться даже с флагом
        for extra in ([], ["--immutable-copy"]):
            result = run(extra, cwd=project)
            assert result.returncode == 2
            assert "-wal" in result.stderr
            assert_no_traceback(result)

    finally:
        writer.close()


def test_hot_journal_is_refused(tmp_path):

    project = make_project(tmp_path, [(1, "static/uploads/a.png")], {"a.png": b"A"})

    (project / "instance" / "paithology.db-journal").write_bytes(b"journal")

    before = snapshot(tmp_path)

    result = run([], cwd=project)

    assert result.returncode == 2
    assert "-journal" in result.stderr
    assert snapshot(tmp_path) == before


# ---------- Ошибки базы ----------

@pytest.mark.parametrize("kind", ["missing", "empty", "corrupt", "directory", "text"])
def test_bad_database_files(tmp_path, kind):

    project = make_project(tmp_path, [], {})

    db = project / "instance" / "paithology.db"
    db.unlink()

    if kind == "empty":
        db.write_bytes(b"")
    elif kind == "corrupt":
        db.write_bytes(b"SQLite format 3\x00" + os.urandom(4000))
    elif kind == "directory":
        db.mkdir()
    elif kind == "text":
        db.write_text("not a database at all " * 20)

    before = snapshot(tmp_path)

    result = run([], cwd=project)

    assert result.returncode == 2
    assert result.stderr.startswith("Ошибка:")
    assert_no_traceback(result)
    assert snapshot(tmp_path) == before

    if kind == "text":
        # Заголовок проверяется до того, как файл увидит SQLite
        assert "не является базой SQLite" in result.stderr


def test_missing_case_table(tmp_path):

    project = make_project(tmp_path, [], {})

    db = project / "instance" / "paithology.db"
    db.unlink()

    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE user (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    result = run([], cwd=project)

    assert result.returncode == 2
    assert "case" in result.stderr
    assert_no_traceback(result)


def test_case_table_without_images_column(tmp_path):

    project = make_project(tmp_path, [], {})

    db = project / "instance" / "paithology.db"
    db.unlink()

    connection = sqlite3.connect(db)
    connection.execute('CREATE TABLE "case" (id INTEGER PRIMARY KEY, user_id INTEGER)')
    connection.commit()
    connection.close()

    result = run([], cwd=project)

    assert result.returncode == 2
    assert "столбц" in result.stderr
    assert_no_traceback(result)


def test_missing_user_table_gives_warning(tmp_path):

    project = make_project(tmp_path, [], {"a.png": b"A"})

    db = project / "instance" / "paithology.db"
    db.unlink()

    connection = sqlite3.connect(db)
    connection.execute('CREATE TABLE "case" (id INTEGER PRIMARY KEY, user_id INTEGER, images TEXT)')
    connection.execute('INSERT INTO "case" (user_id, images) VALUES (5, \'static/uploads/a.png\')')
    connection.commit()
    connection.close()

    report, result = run_json(project)

    assert report["cases"]["owner_deleted"] is None
    assert "user" in result.stderr


def test_locked_database(tmp_path):

    project = make_project(tmp_path, [(1, "static/uploads/a.png")], {"a.png": b"A"})

    db = str(project / "instance" / "paithology.db")

    writer = sqlite3.connect(db, isolation_level=None)

    try:
        writer.execute("BEGIN EXCLUSIVE")

        # Во время эксклюзивной транзакции существует -journal: скрипт должен
        # завершиться понятной ошибкой, а не ждать и не падать
        started = time.monotonic()
        result = run([], cwd=project, timeout=60)
        elapsed = time.monotonic() - started

        assert result.returncode == 2
        assert result.stderr.startswith("Ошибка:")
        assert_no_traceback(result)
        assert elapsed < 30

    finally:
        writer.execute("ROLLBACK")
        writer.close()


def test_locked_database_without_journal_file(tmp_path):

    """Блокировка без файла -journal (например, другой процесс держит RESERVED/EXCLUSIVE)."""

    project = make_project(tmp_path, [(1, "static/uploads/a.png")], {"a.png": b"A"})

    db = str(project / "instance" / "paithology.db")

    writer = sqlite3.connect(db, isolation_level=None)

    try:
        writer.execute("PRAGMA locking_mode=EXCLUSIVE")
        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("COMMIT")

        # После COMMIT журнал удалён, но эксклюзивная блокировка удерживается
        assert not os.path.exists(db + "-journal")

        result = run([], cwd=project, timeout=60)

        assert result.returncode == 2
        assert "заблокирована" in result.stderr
        assert_no_traceback(result)

    finally:
        writer.close()


# ---------- Папка загрузок и корень проекта ----------

def test_uploads_is_a_file(tmp_path):

    project = make_project(tmp_path, [], {})

    fake = tmp_path / "uploads_file"
    fake.write_text("x")

    result = run(["--uploads", str(fake)], cwd=project)

    assert result.returncode == 2
    assert "не является папкой" in result.stderr
    assert_no_traceback(result)


def test_uploads_missing(tmp_path):

    project = make_project(tmp_path, [], {})

    result = run(["--uploads", str(tmp_path / "nope")], cwd=project)

    assert result.returncode == 2
    assert "не найдена" in result.stderr


def test_project_root_required_outside_project(tmp_path):

    project = make_project(tmp_path, [(1, "static/uploads/a.png")], {"a.png": b"A"})

    result = run(
        ["--db", str(project / "instance" / "paithology.db"),
         "--uploads", str(project / "static" / "uploads")],
        cwd=tmp_path
    )

    assert result.returncode == 2
    assert "--project-root" in result.stderr


def test_wrong_project_root_gives_warning(tmp_path):

    project = make_project(
        tmp_path,
        [(1, "static/uploads/a.png"), (1, "static/uploads/b.png")],
        {"a.png": b"A", "b.png": b"B"}
    )

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = run(
        ["--project-root", str(elsewhere),
         "--db", str(project / "instance" / "paithology.db"),
         "--uploads", str(project / "static" / "uploads")],
        cwd=tmp_path
    )

    assert result.returncode == 0
    assert "--project-root" in result.stderr


# ---------- Категории путей ----------

def test_path_categories(tmp_path):

    nfd_name = unicodedata.normalize("NFD", "снимок_й.png")
    nfc_name = unicodedata.normalize("NFC", "снимок_й.png")

    cases = [
        (1, "static/uploads/a.png"),                        # 1 ok
        (1, "C:\\Users\\doc\\static\\uploads\\a.png"),      # 2 Windows-абсолютный
        (1, "\\\\server\\share\\a.png"),                    # 3 UNC
        (1, "/static/uploads/a.png"),                       # 4 URL-стиль
        (1, "https://example.org/a.png"),                   # 5 URL
        (1, "/etc/hostname"),                               # 6 абсолютный
        (1, "../secret.png"),                               # 7 наружу
        (1, "static/uploads/../../secret.png"),             # 8 наружу через ..
        (1, "static/uploads/sub"),                          # 9 папка
        (1, "static/uploads/link.png"),                     # 10 символическая ссылка
        (1, "static/uploads/" + nfc_name),                  # 11 Unicode (на диске NFD)
        (1, "static/uploads/profiles/p.png"),               # 12 служебная подпапка
        (1, "static/uploads\\b.png"),                       # 13 Windows-слэш
        (1, "  ./static/uploads/sub/../a.png  "),           # 14 ok после normpath
        (1, "static/uploads/missing.png"),                  # 15 нет файла
        (1, "static/uploads"),                              # 16 сама папка
    ]

    files = {"a.png": b"A", "b.png": b"B", "sub/x.png": b"X", "profiles/p.png": b"P"}

    project = make_project(tmp_path, cases, files)

    (project / "static" / "uploads" / nfd_name).write_bytes(b"N")
    (tmp_path / "secret.png").write_bytes(b"S")

    if hasattr(os, "symlink"):
        try:
            os.symlink(project / "static" / "uploads" / "a.png",
                       project / "static" / "uploads" / "link.png")
        except OSError:
            pytest.skip("нет прав на создание символических ссылок")

    before = snapshot(tmp_path)

    report, _ = run_json(project)

    assert snapshot(tmp_path) == before

    assert cases_in(report, "windows_absolute") == [2, 3]
    assert cases_in(report, "url_style") == [4, 5]
    assert cases_in(report, "absolute_path") == [6]
    assert cases_in(report, "outside_uploads") == [7, 8]
    assert cases_in(report, "directory") == [9, 16]
    assert cases_in(report, "symlink") == [10]
    assert cases_in(report, "reserved_subfolder") == [12]
    assert cases_in(report, "windows_separators") == [13]

    missing = cases_in(report, "missing")

    # NFC-имя не совпадает с NFD-файлом на Linux; на macOS ФС сама нормализует
    if sys.platform != "darwin":
        assert missing == [11, 15]
        assert cases_in(report, "unicode_candidates") == [11]
    else:
        assert 15 in missing

    by_category = report["references"]["by_category"]

    # 1, 13, 14 найдены как обычные файлы
    assert by_category["ok"] == 3


# ---------- Контрольные суммы ----------

@needs_posix
def test_checksums_skip_fifo_and_symlinks(tmp_path):

    project = make_project(
        tmp_path,
        [(1, "static/uploads/a.png"), (1, "static/uploads/pipe.png"),
         (1, "static/uploads/link.png")],
        {"a.png": b"SAME", "b.png": b"SAME", "c.png": b"OTHER"}
    )

    uploads = project / "static" / "uploads"

    os.mkfifo(uploads / "pipe.png")
    os.symlink(uploads / "c.png", uploads / "link.png")
    os.symlink(tmp_path, uploads / "dirlink")

    before = snapshot(tmp_path)

    # Чтение FIFO заблокировало бы процесс; таймаут ловит зависание
    report, _ = run_json(project, "--checksums")

    assert snapshot(tmp_path) == before

    assert report["files"]["special"] == 1
    assert report["files"]["symlinks"] == 2
    assert cases_in(report, "special") == [2]
    assert cases_in(report, "symlink") == [3]

    # В группах одинакового содержимого только обычные файлы a.png и b.png
    assert len(report["same_content_groups"]) == 1
    assert len(report["same_content_groups"][0]) == 2


def test_hash_helper_refuses_special_and_links(tmp_path):

    module = load_script_module()

    regular = tmp_path / "r.png"
    regular.write_bytes(b"data")

    assert module.sha256_regular_file(str(regular)) == hashlib.sha256(b"data").hexdigest()

    if os.name == "posix":

        fifo = tmp_path / "f.png"
        os.mkfifo(fifo)

        # Отдельный процесс с таймаутом: зависание на FIFO даёт падение теста,
        # а не зависание всего прогона
        code = (
            "import importlib.util, sys\n"
            f"spec = importlib.util.spec_from_file_location('m', {SCRIPT!r})\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            f"print(m.sha256_regular_file({str(fifo)!r}))\n"
        )

        try:
            probe = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=10
            )
        except subprocess.TimeoutExpired:
            pytest.fail("чтение FIFO зависло")

        assert probe.returncode == 0, probe.stderr
        assert probe.stdout.strip() == "None"

        link = tmp_path / "l.png"
        os.symlink(regular, link)

        with pytest.raises(OSError):
            module.sha256_regular_file(str(link))


@needs_posix
def test_unreadable_file_does_not_crash(tmp_path):

    if os.geteuid() == 0:
        pytest.skip("от root права доступа к файлам не действуют")

    project = make_project(
        tmp_path,
        [(1, "static/uploads/a.png"), (1, "static/uploads/locked/y.png")],
        {"a.png": b"A", "x.png": b"X", "locked/y.png": b"Y"}
    )

    secret = project / "static" / "uploads" / "x.png"
    locked = project / "static" / "uploads" / "locked"

    os.chmod(secret, 0)
    os.chmod(locked, 0)

    try:
        report, _ = run_json(project, "--checksums")

        # Нечитаемый файл и закрытая папка
        assert len(report["issues"]["unreadable"]) == 3

        # Файл в закрытой папке — «нет доступа», а не «отсутствует»
        assert cases_in(report, "missing") == []
        assert [i["case_id"] for i in report["issues"]["unreadable"] if "case_id" in i] == [2]

    finally:
        os.chmod(secret, 0o644)
        os.chmod(locked, 0o755)


# ---------- Имена файлов ----------

@needs_posix
def test_undecodable_file_name(tmp_path):

    project = make_project(tmp_path, [(1, "static/uploads/a.png")], {"a.png": b"A"})

    uploads = os.fsencode(project / "static" / "uploads")

    try:
        with open(os.path.join(uploads, b"bad\xff\xfe.png"), "wb") as f:
            f.write(b"B")
    except OSError:
        pytest.skip("файловая система не допускает имена не в UTF-8")

    before = snapshot(tmp_path)

    report, _ = run_json(project, "--checksums")

    assert len(report["issues"]["unused"]) == 1

    for extra in ([], ["--show-filenames"], ["--json", "--show-filenames"]):
        result = run(extra, cwd=project)
        assert result.returncode == 0, result.stderr
        assert_no_traceback(result)

    assert snapshot(tmp_path) == before


def test_pseudonyms_hide_file_names(tmp_path):

    project = make_project(
        tmp_path,
        [(1, "static/uploads/Ivanov_biopsy.png"), (2, "static/uploads/Petrova.png")],
        {"Ivanov_biopsy.png": b"A", "Sidorov_unused.png": b"U"}
    )

    text = run([], cwd=project)
    report, raw = run_json(project)

    output = text.stdout + raw.stdout

    for name in ("Ivanov", "Petrova", "Sidorov"):
        assert name not in output

    labels = re.findall(r"file-\d{4}", output)

    assert labels

    # Нет 12-символьных шестнадцатеричных хешей, по которым подбирается имя
    assert not re.search(r"\b[0-9a-f]{12}\b", output)


def test_no_patient_data_in_output(tmp_path):

    project = make_project(
        tmp_path,
        [(1, "static/uploads/a.png"), (2, "static/uploads/jpg"),
         (1, "static/uploads/jpg"), (None, "")],
        {"a.png": b"A", "jpg": b"J"}
    )

    for extra in ([], ["--json"], ["--checksums"], ["--json", "--checksums"]):

        result = run(extra, cwd=project)

        assert result.returncode == 0, result.stderr

        output = result.stdout + result.stderr

        assert "PHI_" not in output

        for value in PHI.values():
            assert value not in output


# ---------- Дубликаты и подозрительные имена ----------

def test_duplicates_shared_and_suspicious(tmp_path):

    cases = [
        (1, "static/uploads/c.png,static/uploads/c.png"),   # 1 повтор внутри
        (2, "static/uploads/jpg"),                          # 2 только расширение
        (1, "static/uploads/jpg"),                          # 3 общий с 2, другой владелец
        (1, "static/uploads/IMG_0001.jpg"),                 # 4 имя камеры
        (2, "static/uploads/image.png"),                    # 5 типовое слово
        (1, "static/uploads/12345.png"),                    # 6 только цифры
        (1, "static/uploads/biopsy_2024_case.png"),         # 7 обычное имя
    ]

    files = {
        "c.png": b"C", "jpg": b"J", "IMG_0001.jpg": b"I", "image.png": b"M",
        "12345.png": b"D", "biopsy_2024_case.png": b"B", "png": b"unused",
    }

    project = make_project(tmp_path, cases, files)

    report, _ = run_json(project)

    assert cases_in(report, "duplicated_within_case") == [1]

    shared = report["issues"]["shared"]

    assert len(shared) == 1
    assert shared[0]["cases"] == [2, 3]
    assert shared[0]["distinct_owners"] == 2
    assert shared[0]["probable_latest_case_id"] == 3

    suspicious = report["issues"]["suspicious_names"]

    referenced = sorted(item["case_id"] for item in suspicious if item["case_id"])

    assert referenced == [2, 3, 4, 5, 6]

    # Неиспользуемый файл «png» тоже помечен
    assert any(item["case_id"] is None for item in suspicious)


# ---------- Коды завершения и справка ----------

def test_exit_codes_and_fail_on_issues(tmp_path):

    clean = make_project(
        tmp_path / "clean",
        [(1, "static/uploads/biopsy_a.png")],
        {"biopsy_a.png": b"A"}
    )

    for extra in ([], ["--fail-on-issues"], ["--json", "--fail-on-issues"]):
        assert run(extra, cwd=clean).returncode == 0

    report, _ = run_json(clean)

    assert report["issue_count"] == 0

    problems = make_project(
        tmp_path / "problems",
        [(1, "static/uploads/missing_slide.png")],
        {}
    )

    assert run([], cwd=problems).returncode == 0
    assert run(["--fail-on-issues"], cwd=problems).returncode == 1
    assert run(["--json", "--fail-on-issues"], cwd=problems).returncode == 1


def test_help_describes_all_arguments(tmp_path):

    result = run(["--help"], cwd=tmp_path)

    assert result.returncode == 0

    for option in (
        "--project-root", "--db", "--uploads", "--checksums", "--json",
        "--fail-on-issues", "--immutable-copy", "--show-filenames"
    ):
        assert option in result.stdout

    assert "КОПИИ" in result.stdout
    assert "Коды завершения" in result.stdout


def test_unknown_argument(tmp_path):

    result = run(["--dbb", "x"], cwd=tmp_path)

    assert result.returncode == 2
