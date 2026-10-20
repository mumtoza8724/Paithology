"""
Отчёт об исследованиях без владельца.

Считает записи Case, у которых user_id пустой или ссылается на
несуществующего пользователя. База открывается только для чтения,
персональные данные пациентов не выводятся (только id и дата поступления).

Запуск:
    python scripts/report_orphan_cases.py [путь_к_базе]

По умолчанию используется instance/paithology.db.
"""

import os
import sqlite3
import sys


def main():

    db_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        "instance",
        "paithology.db"
    )

    if not os.path.isfile(db_path):
        print(f"База не найдена: {db_path}")
        return 1

    uri = "file:" + os.path.abspath(db_path) + "?mode=ro"

    connection = sqlite3.connect(uri, uri=True)

    try:
        cursor = connection.cursor()

        total = cursor.execute(
            "SELECT COUNT(*) FROM \"case\""
        ).fetchone()[0]

        without_user = cursor.execute(
            "SELECT id, admission_date FROM \"case\" "
            "WHERE user_id IS NULL ORDER BY id"
        ).fetchall()

        missing_user = cursor.execute(
            "SELECT c.id, c.admission_date, c.user_id FROM \"case\" c "
            "LEFT JOIN user u ON u.id = c.user_id "
            "WHERE c.user_id IS NOT NULL AND u.id IS NULL ORDER BY c.id"
        ).fetchall()

    finally:
        connection.close()

    print(f"Всего исследований: {total}")
    print(f"Без владельца (user_id пустой): {len(without_user)}")

    for case_id, admission_date in without_user:
        print(f"  id={case_id} дата поступления={admission_date or '-'}")

    print(
        "Владелец удалён (user_id указывает на несуществующего "
        f"пользователя): {len(missing_user)}"
    )

    for case_id, admission_date, user_id in missing_user:
        print(
            f"  id={case_id} user_id={user_id} "
            f"дата поступления={admission_date or '-'}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
