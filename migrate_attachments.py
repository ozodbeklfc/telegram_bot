"""
Загрузка существующей базы прикреплений в Postgres.

Ожидаемый файл attachments.csv, выгрузка вида:
    Код Контрагента ; Контрагент ; Ziyaret Gunu [; Агент]

Дни визита в выгрузке записаны по-турецки (Pazartesi, Sali, Carsamba...)
и переводятся на русский — в тех же формулировках, что использует бот.

ВАЖНО ПРО КОЛОНКУ С АГЕНТОМ:
без неё не работают обе проверки — «у вас уже 3 дня» и «в точке закреплён
другой агент вашего бренда». Скрипт найдёт колонку с агентом, если она есть
(по названию или по виду значений вроде OR0104), а если её нет — загрузит
строки с пустым агентом и предупредит об этом.

ЗАПУСК:
    python migrate_attachments.py "postgresql://user:pass@host:port/railway"
"""

import csv
import re
import sys
from collections import Counter

import psycopg2
from psycopg2.extras import execute_values

BATCH_SIZE = 500
REPORT_FILE = "otchet_prikrepleniy.csv"

# Турецкие дни недели → русские. Ключи в нижнем регистре и без диакритики,
# чтобы одинаково распознавались «Salı», «Sali» и «SALI».
DAY_MAP = {
    "pazartesi": "Понедельник",
    "sali": "Вторник",
    "carsamba": "Среда",
    "persembe": "Четверг",
    "cuma": "Пятница",
    "cumartesi": "Суббота",
    "pazar": "Воскресенье",
}

# Замена турецких букв на латиницу: Salı → Sali, Çarşamba → Carsamba
TURKISH_LETTERS = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ç": "c", "Ç": "c",
    "ğ": "g", "Ğ": "g", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u",
})

# Возможные названия колонки с агентом
AGENT_HEADERS = ("agent", "агент", "temsilci", "kod agenta", "код агента")

# Логин агента выглядит как две буквы и цифры: OR0104, UL1111
AGENT_PATTERN = re.compile(r"^[A-Za-z]{2}\d{2,6}$")


def normalize_day(raw: str) -> str | None:
    """'Sali ' → 'Вторник'. Возвращает None, если день не распознан."""
    key = (raw or "").strip().translate(TURKISH_LETTERS).lower()
    key = re.sub(r"[^a-z]", "", key)
    return DAY_MAP.get(key)


def read_csv_rows(path: str):
    """Читает CSV, определяя разделитель (';' в выгрузках из Excel)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
        return list(csv.reader(f, delimiter=delimiter)), delimiter


def looks_like_header(row) -> bool:
    """Код контрагента всегда с цифрами, заголовок — без."""
    return bool(row and row[0].strip()) and not any(c.isdigit() for c in row[0])


def find_agent_column(header, rows) -> int:
    """Ищет колонку с агентом: сначала по названию, затем по виду значений."""
    for i, name in enumerate(header):
        if name.strip().lower() in AGENT_HEADERS:
            return i

    # Названия колонок могут быть любыми — смотрим на сами данные
    width = max((len(r) for r in rows[:200]), default=0)
    for col in range(width):
        values = [r[col].strip() for r in rows[:200] if len(r) > col and r[col].strip()]
        if values and sum(bool(AGENT_PATTERN.match(v)) for v in values) > len(values) * 0.8:
            return col

    return -1


def main():
    if len(sys.argv) < 2:
        print('Использование: python migrate_attachments.py "<строка подключения>"')
        sys.exit(1)

    path = sys.argv[2] if len(sys.argv) > 2 else "attachments.csv"

    print("Подключаюсь к Postgres...")
    try:
        conn = psycopg2.connect(sys.argv[1], connect_timeout=15)
    except psycopg2.OperationalError as e:
        print(f"\n❌ Не удалось подключиться: {e}")
        print("   Строку подключения бери в Railway → Postgres → Variables → DATABASE_PUBLIC_URL")
        sys.exit(1)
    print("✅ Подключение установлено.\n")

    rows, delimiter = read_csv_rows(path)
    print(f"Файл: {path}   разделитель: '{delimiter}'")

    if rows and looks_like_header(rows[0]):
        header, rows, offset = rows[0], rows[1:], 2
        print(f"Заголовок: {' | '.join(header)}")
    else:
        header, offset = [], 1
        print("⚠️  Заголовок не найден — первая строка считается данными")

    agent_col = find_agent_column(header, rows)
    if agent_col >= 0:
        name = header[agent_col] if agent_col < len(header) else f"колонка {agent_col + 1}"
        print(f"Агент берётся из «{name}»")
    else:
        print("\n⚠️  КОЛОНКА С АГЕНТОМ НЕ НАЙДЕНА")
        print("   Строки загрузятся с пустым агентом, и проверки «у вас уже 3 дня»")
        print("   и «в точке уже другой агент вашего бренда» для них работать НЕ будут.")
        print("   Добавь колонку с логином агента (OR0104) и перезапусти.\n")

    data = []
    report = []
    counter = Counter()
    unknown_days = Counter()

    for i, row in enumerate(rows):
        line_no = i + offset

        if not any(cell.strip() for cell in row):
            continue

        if len(row) < 3:
            report.append((line_no, " | ".join(row), "меньше трёх колонок"))
            counter["меньше трёх колонок"] += 1
            continue

        point_code = row[0].strip()
        point_name = row[1].strip()
        raw_days = row[2].strip()

        if not point_code:
            report.append((line_no, " | ".join(row), "пустой код контрагента"))
            counter["пустой код контрагента"] += 1
            continue

        # В одной ячейке может быть несколько дней через запятую
        days, bad = [], []
        for part in re.split(r"[,/;]", raw_days):
            if not part.strip():
                continue
            day = normalize_day(part)
            if day:
                days.append(day)
            else:
                bad.append(part.strip())

        if bad:
            unknown_days.update(bad)
            report.append((line_no, " | ".join(row), f"не распознан день: {', '.join(bad)}"))
            counter["не распознан день визита"] += 1

        if not days:
            continue

        agent = row[agent_col].strip().upper() if agent_col >= 0 and len(row) > agent_col else ""
        brand = agent[:2] if agent else ""

        data.append((point_code, point_name, brand, agent or None, ", ".join(days)))

    if data:
        cur = conn.cursor()
        # Старые прикрепления заменяем целиком: файл — источник истины
        cur.execute("TRUNCATE attachments RESTART IDENTITY")
        for i in range(0, len(data), BATCH_SIZE):
            execute_values(cur, """
                INSERT INTO attachments (point_code, point_name, agent_brand, agent, visit_day)
                VALUES %s
            """, data[i:i + BATCH_SIZE])
            conn.commit()
            print(f"   Загрузка: {min(i + BATCH_SIZE, len(data))}/{len(data)}", end="\r", flush=True)
        cur.close()
        print(" " * 50, end="\r")

    conn.close()

    print(f"\nСтрок в файле: {len(rows)}")
    print(f"✅ Загружено:   {len(data)}")
    print(f"⚠️  С замечаниями: {len(report)}")
    for reason, count in counter.most_common():
        print(f"   • {reason}: {count}")
    if unknown_days:
        print(f"   Нераспознанные значения дней: {dict(unknown_days.most_common(10))}")

    if report:
        with open(REPORT_FILE, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Строка в файле", "Данные", "Причина"])
            w.writerows(report)
        print(f"\n📄 Подробности: {REPORT_FILE}")

    print("🎉 Готово.")


if __name__ == "__main__":
    main()
