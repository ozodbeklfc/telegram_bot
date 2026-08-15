"""
Загрузка данных из выгрузок Google Таблицы в Postgres.

Скрипт можно запускать сколько угодно раз: существующие записи обновляются,
новые добавляются.

ГЛАВНОЕ ОТЛИЧИЕ ОТ ПЕРВОЙ ВЕРСИИ:
раньше «лишние» строки молча пропускались, и было непонятно, почему в базе
оказалось меньше записей, чем в файле. Теперь по каждой строке записывается
причина — на экран выводится сводка, а полный список сохраняется в файл
otchet_migracii.csv (номер строки, данные, причина).

ПОДГОТОВКА:
1. Лист "Пользователи"     → Файл → Скачать → CSV → users.csv
2. Лист "Клиентская база"  → Файл → Скачать → CSV → client_base.csv
3. Положить оба файла рядом с этим скриптом

ВАЖНО: перед первым запуском выполнить schema_update.sql — без колонок
status и inn_raw загрузка не пойдёт.

ЗАПУСК:
    python migrate_from_csv.py "postgresql://user:pass@host:port/railway"
"""

import csv
import re
import sys
import time
from collections import Counter

import psycopg2
from psycopg2.extras import execute_values

BATCH_SIZE = 500
REPORT_FILE = "otchet_migracii.csv"

# Бот принимает ввод только из 9 или 14 цифр. Строки с другой длиной
# загружаются, но попадают в отчёт — найти такую точку агент не сможет.
VALID_INN_LENGTHS = (9, 14)


def chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def clean_inn(raw: str) -> str:
    """Оставляет только цифры: '306955509+' → '306955509'."""
    return re.sub(r"[^0-9]", "", raw or "")


class Report:
    """Копит причины, по которым строки не попали в базу или требуют внимания."""

    def __init__(self):
        self.rows = []
        self.counter = Counter()

    def add(self, line_no, data, reason: str, group: str = None):
        """
        reason — подробная причина для конкретной строки (попадает в файл).
        group  — как эту причину назвать в сводке на экране. Нужен, чтобы
                 десять разных «тот же ИНН, что в строке N» не занимали
                 десять строк сводки.
        """
        self.rows.append((line_no, " | ".join(str(x) for x in data), reason))
        self.counter[group or reason] += 1

    def print_summary(self):
        if not self.counter:
            print("   Замечаний нет.")
            return
        for reason, count in self.counter.most_common():
            print(f"     • {reason}: {count}")


def load_batches(conn, sql: str, data: list):
    total = len(data)
    cur = conn.cursor()
    start = time.time()

    for i, batch in enumerate(chunked(data, BATCH_SIZE), start=1):
        execute_values(cur, sql, batch)
        conn.commit()  # фиксируем после каждой пачки — прогресс не потеряется
        print(f"   Загрузка: {min(i * BATCH_SIZE, total)}/{total}", end="\r", flush=True)

    cur.close()
    print(" " * 60, end="\r")
    return time.time() - start


# ======================================================================
# ПОЛЬЗОВАТЕЛИ
# ======================================================================

def migrate_users(conn, path: str = "users.csv"):
    print("\n=== ПОЛЬЗОВАТЕЛИ ===")
    report = Report()

    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    if rows and rows[0] and rows[0][0].strip().lower() in ("login", "логин"):
        rows, offset = rows[1:], 2
    else:
        offset = 1

    data, seen = [], {}
    for i, row in enumerate(rows):
        line_no = i + offset

        if len(row) < 2 or not row[0].strip():
            report.add(line_no, row, "пустой логин")
            continue
        if not row[1].strip():
            report.add(line_no, row, "пустой пароль")
            continue

        login, password = row[0].strip().lower(), row[1].strip()

        if login in seen:
            report.add(line_no, row, f"повтор логина '{login}' — оставлена последняя строка",
                       group="повтор логина")
            data[seen[login]] = (login, password)
            continue

        seen[login] = len(data)
        data.append((login, password))

    if data:
        load_batches(conn, """
            INSERT INTO users (login, password) VALUES %s
            ON CONFLICT (login) DO UPDATE SET password = EXCLUDED.password
        """, data)

    print(f"   Строк в файле: {len(rows)}")
    print(f"   ✅ Загружено:   {len(data)}")
    print(f"   ⚠️  Не принято:  {len(report.rows)}")
    report.print_summary()
    return report


# ======================================================================
# КЛИЕНТСКАЯ БАЗА
# ======================================================================

def migrate_client_base(conn, path: str = "client_base.csv"):
    print("\n=== КЛИЕНТСКАЯ БАЗА ===")
    report = Report()

    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    if rows and len(rows[0]) > 2 and rows[0][2].strip().lower() in ("inn", "инн"):
        rows, offset = rows[1:], 2
    else:
        offset = 1

    data, seen = [], {}
    passive_count = 0
    skipped = 0

    for i, row in enumerate(rows):
        line_no = i + offset

        if len(row) < 3:
            report.add(line_no, row, "в строке меньше трёх колонок"); skipped += 1
            continue

        raw_inn = row[2].strip()
        if not raw_inn:
            report.add(line_no, row, "пустой ИНН"); skipped += 1
            continue

        if "E+" in raw_inn.upper():
            # '3,14E+13' — Excel превратил длинный номер в научную нотацию,
            # исходные цифры из такого значения восстановить невозможно
            report.add(line_no, row, "ИНН испорчен Excel (научная нотация)"); skipped += 1
            continue

        inn = clean_inn(raw_inn)
        if not inn:
            # Например, 'NE STAV INN' — текст вместо номера
            report.add(line_no, row, "в ИНН нет ни одной цифры"); skipped += 1
            continue

        # Пометки вокруг номера ('306955509+', '+629441139', '306822032++')
        # ставит финансовый отдел — так помечены пассивные точки
        status = 1 if raw_inn != inn else 0
        if status:
            passive_count += 1

        if len(inn) not in VALID_INN_LENGTHS:
            report.add(line_no, row,
                       f"необычная длина ИНН ({len(inn)} цифр) — строка загружена, "
                       f"но бот принимает только 9 или 14 цифр",
                       group="необычная длина ИНН — точку не найти через бота")

        if inn in seen:
            prev_line, prev_raw, idx = seen[inn]
            report.add(line_no, row,
                       f"тот же ИНН, что в строке {prev_line} ('{prev_raw}') — оставлена последняя",
                       group="ИНН повторяется в файле — оставлена последняя строка")
            data[idx] = (row[0].strip(), row[1].strip(), inn, raw_inn, status)
            skipped += 1
            continue

        seen[inn] = (line_no, raw_inn, len(data))
        data.append((row[0].strip(), row[1].strip(), inn, raw_inn, status))

    if data:
        elapsed = load_batches(conn, """
            INSERT INTO client_base (point_code, point_name, inn, inn_raw, status)
            VALUES %s
            ON CONFLICT (inn) DO UPDATE
                SET point_code = EXCLUDED.point_code,
                    point_name = EXCLUDED.point_name,
                    inn_raw    = EXCLUDED.inn_raw,
                    status     = EXCLUDED.status
        """, data)
        print(f"   Время загрузки: {elapsed:.1f} сек")

    print(f"   Строк в файле:  {len(rows)}")
    print(f"   ✅ Загружено:    {len(data)}")
    print(f"   ⚠️  Не принято:   {skipped}")
    print(f"   🔴 Пассивных:    {passive_count} (символы в ИНН)")
    print("   Замечания:")
    report.print_summary()
    return report


def save_report(reports):
    rows = [r for rep in reports for r in rep.rows]
    if not rows:
        return 0
    with open(REPORT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Строка в файле", "Данные", "Причина"])
        w.writerows(rows)
    return len(rows)


def main():
    if len(sys.argv) < 2:
        print('Использование: python migrate_from_csv.py "<строка подключения к Postgres>"')
        sys.exit(1)

    print("Подключаюсь к Postgres...")
    conn = psycopg2.connect(sys.argv[1], connect_timeout=15)
    print("✅ Подключение установлено.")

    # Без новых колонок загрузка упадёт на первой же пачке — проверяем заранее
    cur = conn.cursor()
    cur.execute("""
        SELECT column_name FROM information_schema.columns
         WHERE table_name = 'client_base' AND column_name IN ('status', 'inn_raw')
    """)
    found = {r[0] for r in cur.fetchall()}
    cur.close()
    if len(found) < 2:
        print("\n❌ В таблице client_base нет колонок status и inn_raw.")
        print("   Сначала выполни schema_update.sql (Railway → Postgres → Query).")
        conn.close()
        sys.exit(1)

    reports = []
    for name, func, filename in (("ПОЛЬЗОВАТЕЛИ", migrate_users, "users.csv"),
                                 ("КЛИЕНТСКАЯ БАЗА", migrate_client_base, "client_base.csv")):
        try:
            reports.append(func(conn))
        except FileNotFoundError:
            print(f"\n=== {name} ===\n   Файл {filename} не найден — пропускаю.")

    conn.close()

    count = save_report(reports)
    print("\n" + "=" * 58)
    if count:
        print(f"📄 Подробности по {count} строкам: {REPORT_FILE}")
        print("   (открывается в Excel: строка, данные, причина)")
    print("🎉 Готово.")


if __name__ == "__main__":
    main()
