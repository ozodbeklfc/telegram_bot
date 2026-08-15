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


def read_csv_rows(path: str):
    """
    Читает CSV, сам определяя разделитель.

    Выгрузка из Excel в русской локали разделяет колонки точкой с запятой,
    и при чтении через запятую вся строка выглядела бы одной колонкой —
    именно из-за этого миграция отбрасывала все 27 тысяч строк.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)

        # Считаем, чего в файле больше: ';' или ','
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
        rows = list(csv.reader(f, delimiter=delimiter))

    return rows, delimiter


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

    rows, _ = read_csv_rows(path)

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

    rows, delimiter = read_csv_rows(path)
    print(f"   Разделитель колонок: '{delimiter}'")

    # Заголовок: Код Контрагента ; Название ; ИНН ; Статус
    if rows and len(rows[0]) > 2 and rows[0][2].strip().lower() in ("inn", "инн"):
        header, rows, offset = rows[0], rows[1:], 2
    else:
        header, offset = [], 1

    has_status_column = len(header) > 3
    if has_status_column:
        print("   Статус берётся из четвёртой колонки файла")
    else:
        print("   Колонки «Статус» нет — пассивными считаются точки с символами в ИНН")

    data, seen = [], {}
    passive_count = no_inn_count = skipped = 0

    for i, row in enumerate(rows):
        line_no = i + offset

        if not any(cell.strip() for cell in row):
            continue  # пустая строка в конце файла — не ошибка

        if len(row) < 2:
            report.add(line_no, row, "в строке меньше двух колонок"); skipped += 1
            continue

        point_code = row[0].strip()
        point_name = row[1].strip()

        if not point_code:
            report.add(line_no, row, "пустой код контрагента"); skipped += 1
            continue

        raw_inn = row[2].strip() if len(row) > 2 else ""

        if "E+" in raw_inn.upper():
            # '3,14E+13' — Excel превратил длинный номер в научную нотацию,
            # исходные цифры восстановить невозможно
            report.add(line_no, row, "ИНН испорчен Excel (научная нотация)"); skipped += 1
            continue

        inn = clean_inn(raw_inn) or None

        if inn is None:
            # Служебные строки (DILERLER, PERSONAL ZAKAZI) идут без ИНН.
            # Раньше они отбрасывались — теперь загружаются с NULL.
            no_inn_count += 1
            if raw_inn:
                report.add(line_no, row, f"в ИНН нет цифр ('{raw_inn}') — записан NULL",
                           group="ИНН без цифр — записан NULL")

        # Статус: из файла, если колонка есть; иначе по символам вокруг номера
        if has_status_column and len(row) > 3 and row[3].strip():
            status = 1 if row[3].strip() not in ("0", "") else 0
        else:
            status = 1 if raw_inn and raw_inn != inn else 0
        if status:
            passive_count += 1

        if inn and len(inn) not in VALID_INN_LENGTHS:
            report.add(line_no, row,
                       f"необычная длина ИНН ({len(inn)} цифр) — строка загружена, "
                       f"но бот принимает только 9 или 14 цифр",
                       group="необычная длина ИНН — точку не найти через бота")

        # Уникален код контрагента, а не ИНН: у одной точки бывает несколько
        # строк с одним ИНН и разными кодами (KALINA, UNILEVER, NIVEA...)
        if point_code in seen:
            prev_line, idx = seen[point_code]
            report.add(line_no, row,
                       f"код контрагента повторяет строку {prev_line} — оставлена последняя",
                       group="повтор кода контрагента — оставлена последняя строка")
            data[idx] = (point_code, point_name, inn, status)
            skipped += 1
            continue

        seen[point_code] = (line_no, len(data))
        data.append((point_code, point_name, inn, status))

    if data:
        # Строки без ИНН нельзя обновить через ON CONFLICT по ИНН, а по коду
        # контрагента — можно: он уникален и есть у каждой строки
        elapsed = load_batches(conn, """
            INSERT INTO client_base (point_code, point_name, inn, status)
            VALUES %s
            ON CONFLICT (point_code) DO UPDATE
                SET point_name = EXCLUDED.point_name,
                    inn        = EXCLUDED.inn,
                    status     = EXCLUDED.status
        """, data)
        print(f"   Время загрузки: {elapsed:.1f} сек")

    print(f"   Строк в файле:  {len(rows)}")
    print(f"   ✅ Загружено:    {len(data)}")
    print(f"   ⚠️  Не принято:   {skipped}")
    print(f"   🔴 Пассивных:    {passive_count}")
    print(f"   ➖ Без ИНН:      {no_inn_count} (записаны как NULL)")
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
    try:
        conn = psycopg2.connect(sys.argv[1], connect_timeout=15)
    except psycopg2.OperationalError as e:
        print(f"\n❌ Не удалось подключиться: {e}")
        print("   Строку подключения бери в Railway → сервис Postgres → Variables →")
        print("   DATABASE_PUBLIC_URL (именно публичный: скрипт работает с твоего компьютера,")
        print("   а обычный DATABASE_URL доступен только изнутри Railway).")
        print('   Пример: python migrate_from_csv.py "postgresql://postgres:ПАРОЛЬ@xxx.proxy.rlwy.net:12345/railway"')
        sys.exit(1)
    print("✅ Подключение установлено.")

    # Без новых колонок загрузка упадёт на первой же пачке — проверяем заранее
    cur = conn.cursor()
    cur.execute("""
        SELECT column_name FROM information_schema.columns
         WHERE table_name = 'client_base' AND column_name = 'status'
    """)
    has_status = cur.fetchone() is not None

    # Уникальность должна стоять на коде контрагента, иначе строки с общим
    # ИНН (KALINA, UNILEVER, NIVEA...) затрут друг друга
    cur.execute("""
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'client_base'::regclass
           AND contype = 'u'
           AND pg_get_constraintdef(oid) LIKE '%point_code%'
    """)
    has_code_unique = cur.fetchone() is not None
    cur.close()

    if not (has_status and has_code_unique):
        print("\n❌ Структура client_base устарела:")
        if not has_status:
            print("   • нет колонки status")
        if not has_code_unique:
            print("   • уникальность стоит не на коде контрагента")
        print("   Выполни schema_update.sql (Railway → Postgres → Query) и повтори.")
        conn.close()
        sys.exit(1)

    # Список из кортежей. Чтобы грузить ТОЛЬКО клиентскую базу — убери
    # первую строку (или просто удали users.csv из папки: файл не найдётся
    # и шаг пропустится сам).
    TASKS = [
        ("ПОЛЬЗОВАТЕЛИ",    migrate_users,       "users.csv"),
        ("КЛИЕНТСКАЯ БАЗА", migrate_client_base, "client_base.csv"),
    ]

    reports = []
    for name, func, filename in TASKS:
        try:
            reports.append(func(conn))
        except FileNotFoundError:
            print(f"   Файл {filename} не найден — пропускаю.")

    conn.close()

    count = save_report(reports)
    print("\n" + "=" * 58)
    if count:
        print(f"📄 Подробности по {count} строкам: {REPORT_FILE}")
        print("   (открывается в Excel: строка, данные, причина)")
    print("🎉 Готово.")


if __name__ == "__main__":
    main()
