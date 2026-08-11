"""
Одноразовый скрипт миграции данных из Google Таблицы в Postgres.
Версия с диагностикой: небольшие пачки (300 строк), промежуточный прогресс
в консоли, commit после каждой пачки (если оборвётся на середине — уже
перенесённое не потеряется, можно будет просто перезапустить).

ПОДГОТОВКА:
1. Открой Google Таблицу
2. Лист "Пользователи" → Файл → Скачать → CSV → сохрани как users.csv
3. Лист "Клиентская база" → Файл → Скачать → CSV → сохрани как client_base.csv
4. Положи оба файла рядом с этим скриптом

ЗАПУСК:
    python migrate_from_csv.py "postgresql://user:pass@host:port/railway"
"""

import csv
import sys
import time

import psycopg2
from psycopg2.extras import execute_values

BATCH_SIZE = 300


def chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def migrate_users(conn, path: str = "users_n.csv"):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if rows and rows[0][0].strip().lower() in ("login", "логин"):
        rows = rows[1:]

    data = []
    for row in rows:
        if len(row) < 2 or not row[0].strip():
            continue
        data.append((row[0].strip().lower(), row[1].strip()))

    if not data:
        print("⚠️  Пользователи: нет данных для переноса")
        return

    cur = conn.cursor()
    execute_values(
        cur,
        """
        INSERT INTO users (login, password)
        VALUES %s
        ON CONFLICT (login) DO UPDATE SET password = EXCLUDED.password
        """,
        data,
        page_size=BATCH_SIZE,
    )
    conn.commit()
    cur.close()
    print(f"✅ Пользователи: перенесено {len(data)} строк")


def migrate_client_base(conn, path: str = "client_base.csv"):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if rows and len(rows[0]) > 2 and rows[0][2].strip().lower() in ("inn", "инн"):
        rows = rows[1:]

    data = []
    skipped = 0
    seen_inn = set()
    for row in rows:
        if len(row) < 3 or not row[2].strip():
            skipped += 1
            continue
        inn = row[2].strip()
        if inn in seen_inn:
            skipped += 1
            continue  # дубликат ИНН внутри самого файла — пропускаем, иначе Postgres ругнётся
        seen_inn.add(inn)
        data.append((row[0].strip(), row[1].strip(), inn))

    if not data:
        print("⚠️  Клиентская база: нет данных для переноса")
        return

    total = len(data)
    batches = list(chunked(data, BATCH_SIZE))
    print(f"Загружаю {total} строк, пачками по {BATCH_SIZE} ({len(batches)} пачек)...\n")

    cur = conn.cursor()
    start_all = time.time()

    for i, batch in enumerate(batches, start=1):
        t0 = time.time()
        execute_values(
            cur,
            """
            INSERT INTO client_base (point_code, point_name, inn)
            VALUES %s
            ON CONFLICT (inn) DO UPDATE
                SET point_code = EXCLUDED.point_code,
                    point_name = EXCLUDED.point_name
            """,
            batch,
        )
        conn.commit()  # фиксируем после каждой пачки — прогресс не потеряется
        elapsed = time.time() - t0
        done = min(i * BATCH_SIZE, total)
        print(f"  Пачка {i}/{len(batches)} ({done}/{total} строк) — {elapsed:.2f} сек", flush=True)

    cur.close()
    total_elapsed = time.time() - start_all
    print(f"\n✅ Клиентская база: перенесено {total} строк за {total_elapsed:.1f} сек (пропущено: {skipped})")


def main():
    if len(sys.argv) < 2:
        print("Использование: python migrate_from_csv.py \"<строка подключения к Postgres>\"")
        sys.exit(1)

    conn_string = sys.argv[1]
    print("Подключаюсь к Postgres...")
    conn = psycopg2.connect(conn_string, connect_timeout=10)
    print("✅ Подключение установлено.\n")

    migrate_users(conn)
    migrate_client_base(conn)

    conn.close()
    print("\n🎉 Миграция завершена.")


if __name__ == "__main__":
    main()
