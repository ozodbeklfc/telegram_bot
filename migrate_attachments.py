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
import io
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


# Кодировки, в которых встречаются выгрузки. Порядок важен.
#
# Excel в русской локали сохраняет CSV в cp1251, и байт «К» (0xCA) ломает
# чтение в UTF-8 с ошибкой «invalid continuation byte». В турецкой локали
# тот же файл окажется в cp1254. Поэтому кодировку не задаём жёстко,
# а подбираем.
ENCODINGS = ("utf-8-sig", "cp1251", "cp1254", "latin-1")


# Буквы, по которым узнаётся правильно прочитанный текст
CYRILLIC = re.compile(r"[\u0400-\u04FF]")
TURKISH = re.compile(r"[ğĞşŞıİ]")
# Латиница с диакритикой, которой в наших выгрузках взяться неоткуда:
# так выглядит кириллица, прочитанная чужой однобайтовой кодировкой
MOJIBAKE = re.compile(r"[àáâãäåæçèéêëìíîïðñòóôõöøùúûýþÿÀÁÂÃÄÅÆÈÉÊËÌÍÎÏÐÑÒÓÔÕØÙÚÛÝÞ]")
# Слово, где кириллица и латиница вперемешку — «Ьnvanэ». Внутри одного
# слова такого не бывает, это верный признак неверной кодировки
MIXED = re.compile(r"\b(?=\w*[\u0400-\u04FF])(?=\w*[A-Za-z])\w+\b")


def _score_text(text: str) -> int:
    """
    Насколько осмысленно выглядит текст после расшифровки.

    Нужен, потому что ошибку чтения однобайтовые кодировки не выдают:
    cp1251 и cp1254 проглотят любые байты, просто буквы получатся разные.
    Поэтому выбираем не «первую подошедшую», а самую правдоподобную —
    иначе турецкая выгрузка молча превратилась бы в «Cari Ьnvanэ».
    """
    return (len(CYRILLIC.findall(text))
            + len(TURKISH.findall(text)) * 3
            - len(MOJIBAKE.findall(text))
            - len(MIXED.findall(text)) * 5)


def read_text(path: str):
    """
    Читает файл, сам подбирая кодировку. Возвращает (текст, кодировка).

    UTF-8 проверяется первым: если файл в нём, вопрос закрыт. Если нет,
    из однобайтовых кодировок выбирается та, где получилось больше
    кириллицы — у cp1251 и cp1254 одни и те же байты значат разные буквы,
    и ошибку чтения ни одна из них не выдаст. Считать «подошла первая»
    здесь нельзя: турецкий файл молча превратился бы в кракозябры.
    """
    raw = open(path, "rb").read()

    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass

    best, best_enc, best_score = None, None, None
    for enc in ENCODINGS[1:]:
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        score = _score_text(text)
        if best_score is None or score > best_score:
            best, best_enc, best_score = text, enc, score

    if best is None:
        best, best_enc = raw.decode("latin-1"), "latin-1"

    return best, best_enc


def read_csv_rows(path: str):
    """Читает CSV, определяя разделитель (';' в выгрузках из Excel)."""
    text, encoding = read_text(path)

    # Считаем, чего в файле больше: ';' или ','
    sample = text[:4096]
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","

    print(f"Кодировка файла: {encoding}, разделитель: '{delimiter}'")
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    return rows, delimiter


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
    skipped_empty = 0        # пустые строки
    skipped_no_code = 0      # нет кода контрагента
    skipped_no_days = 0      # ни один день не распознан
    loaded_rows = 0          # строк файла, попавших в базу

    for i, row in enumerate(rows):
        line_no = i + offset

        if not any(cell.strip() for cell in row):
            skipped_empty += 1
            continue

        point_code = row[code_col].strip() if len(row) > code_col else ""
        point_name = row[name_col].strip() if name_col >= 0 and len(row) > name_col else ""
        raw_days = row[day_col].strip() if len(row) > day_col else ""

        if not point_code:
            report.append((line_no, " | ".join(row), "пустой код контрагента"))
            counter["пустой код контрагента"] += 1
            skipped_no_code += 1
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
            # Ни одного пригодного дня — строку загрузить не во что
            skipped_no_days += 1
            if not bad:
                report.append((line_no, " | ".join(row), "день визита не указан"))
                counter["день визита не указан"] += 1
            continue

        agent = row[agent_col].strip().upper() if agent_col >= 0 and len(row) > agent_col else ""
        # Бренд — ровно первые два символа: UL0112 и ULTP0101 — один бренд UL
        brand = agent[:2] if agent else ""

        # Каждый день визита сохраняем отдельной строкой
        loaded_rows += 1
        for day in days:
            data.append((point_code, point_name, brand, agent or None, day))

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

    total_skipped = skipped_empty + skipped_no_code + skipped_no_days

    print(f"\nСтрок с данными:  {len(rows)}")
    print(f"✅ Обработано:     {loaded_rows}")
    print(f"⚠️  Пропущено:      {total_skipped}")
    print(f"📌 Записей в базе: {len(data)} (по одной на каждый день визита)")
    if skipped_no_days:
        print(f"     • день визита не распознан или пуст: {skipped_no_days}")
    if skipped_no_code:
        print(f"     • нет кода контрагента: {skipped_no_code}")
    if skipped_empty:
        print(f"     • пустые строки: {skipped_empty}")

    # Числа должны сходиться — иначе где-то потеря, о которой лучше знать
    if loaded_rows + total_skipped != len(rows):
        print(f"   ⚠️  Баланс не сходится: {loaded_rows} + {total_skipped} != {len(rows)}")

    if counter:
        print("   Замечания:")
        for reason, count in counter.most_common():
            print(f"     • {reason}: {count}")
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
