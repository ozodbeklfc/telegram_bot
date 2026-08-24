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

# Логин агента: буквы и цифры — OR0104, UL1111, BAH001
AGENT_PATTERN = re.compile(r"^[A-Za-z]{2,5}\d{2,6}$")

# Код контрагента: цифры с точками — 120.01.101.0267
CODE_PATTERN = re.compile(r"^\d[\d.]{4,}$")


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


def detect_columns(header, rows) -> dict:
    """
    Определяет, что в какой колонке лежит, по САМИМ ЗНАЧЕНИЯМ, а не по
    порядку: в разных выгрузках агент стоит то первым столбцом, то последним.

    Признаки однозначные:
      код контрагента — цифры с точками (120.01.101.0267)
      агент           — буквы + цифры (OR0104, BAH001)
      день визита     — распознаётся словарём турецких дней
      название        — то, что осталось
    """
    width = max((len(r) for r in rows[:300]), default=0)
    scores = []

    for col in range(width):
        values = [r[col].strip() for r in rows[:300] if len(r) > col and r[col].strip()]
        if not values:
            scores.append({"code": 0, "agent": 0, "day": 0})
            continue
        n = len(values)
        scores.append({
            "code":  sum(bool(CODE_PATTERN.match(v)) for v in values) / n,
            "agent": sum(bool(AGENT_PATTERN.match(v)) for v in values) / n,
            # День может быть записан как "Pazartesi, Cuma" — берём первую часть
            "day":   sum(bool(normalize_day(re.split(r"[,/;]", v)[0])) for v in values) / n,
        })

    def best(kind, used):
        candidates = [(i, sc[kind]) for i, sc in enumerate(scores)
                      if i not in used and sc[kind] > 0.6]
        if not candidates:
            return -1
        return max(candidates, key=lambda x: x[1])[0]

    used = set()
    result = {}
    # Порядок важен: сначала самые узнаваемые типы
    for kind in ("day", "code", "agent"):
        idx = best(kind, used)
        result[kind] = idx
        if idx >= 0:
            used.add(idx)

    # Название — первая незанятая колонка, где есть буквы
    result["name"] = -1
    for col in range(width):
        if col in used:
            continue
        values = [r[col].strip() for r in rows[:300] if len(r) > col and r[col].strip()]
        if values and any(any(c.isalpha() for c in v) for v in values):
            result["name"] = col
            break

    return result


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

    cols = detect_columns(header, rows)
    code_col, name_col, day_col, agent_col = cols["code"], cols["name"], cols["day"], cols["agent"]

    def col_label(i):
        if i < 0:
            return "не найдена"
        return header[i] if i < len(header) else f"колонка {i + 1}"

    print(f"Колонки: код={col_label(code_col)}, название={col_label(name_col)}, "
          f"день={col_label(day_col)}, агент={col_label(agent_col)}")

    if code_col < 0 or day_col < 0:
        print("\n❌ Не удалось найти колонку с кодом контрагента или днём визита.")
        print("   Проверь файл: код должен быть вида 120.01.101.0267,")
        print("   день — Pazartesi / Sali / Carsamba и т.д.")
        conn.close()
        sys.exit(1)

    if agent_col < 0:
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

        point_code = row[code_col].strip() if len(row) > code_col else ""
        point_name = row[name_col].strip() if name_col >= 0 and len(row) > name_col else ""
        raw_days = row[day_col].strip() if len(row) > day_col else ""

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
        # Бренд — буквенная часть логина: OR0104 → OR, BAH001 → BAH
        brand = re.match(r"^[A-Z]+", agent).group(0) if agent else ""

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
