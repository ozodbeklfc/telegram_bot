"""
Загрузка структуры «супервайзер → агент» из SUPERVISORS.xlsx в Postgres.

ФОРМАТ ФАЙЛА
    Один лист на бренд (LEVER, ORIMI, NIVEA, FM, MX, KALINA, JD...).
    На листе шапка с колонками:
        № | Территория | Код супервайзера | Код агента | Роль
    Строка супервайзера: код супервайзера заполнен, код агента пуст,
                         роль «Супервайзер».
    Строка агента:       заполнены оба кода, роль «Агент».

    Колонки ищутся ПО НАЗВАНИЮ, а не по номеру: в разных листах их порядок
    может отличаться, а шапка не всегда в первой строке (сверху бывает
    заголовок таблицы). Скрипт сам находит строку шапки.

ЧТО ДЕЛАЕТ
    1. читает все листы книги;
    2. заводит учётные записи супервайзерам (role='supervisor');
    3. проставляет агентам колонку supervisor и бренд;
    4. пишет отчёт обо всём, что выглядит подозрительно.

ВАЖНО: скрипт НИЧЕГО не удаляет. Агент, которого нет в файле, остаётся
в базе со старым супервайзером — такие попадают в отчёт отдельной строкой.

ПЕРЕД ЗАПУСКОМ выполнить supervisor_setup.sql.
Территория сохраняется, только если в users есть колонка territory:
    ALTER TABLE users ADD COLUMN IF NOT EXISTS territory TEXT;

ЗАПУСК
    pip install openpyxl psycopg2-binary

    # сначала посмотреть, что получится, НИЧЕГО не записывая:
    python migrate_supervisors.py "postgresql://..." --dry-run

    # затем записать:
    python migrate_supervisors.py "postgresql://..."

Файл SUPERVISORS.xlsx должен лежать рядом со скриптом.
"""

import csv
import re
import sys
from collections import Counter, defaultdict

import openpyxl
import psycopg2
from psycopg2.extras import execute_values

SRC = "SUPERVISORS.xlsx"
REPORT = "otchet_supervayzerov.csv"

# Пароль по умолчанию для новых учёток.
# Каждый меняет его сам при первом входе в панель.
DEFAULT_PASSWORD = "123"

# Сколько первых строк листа просматривать в поисках шапки.
# Над таблицей бывает заголовок и пустые строки, но не десяток.
HEADER_SCAN_ROWS = 15

# Названия колонок. Сверяются в нижнем регистре и без лишних пробелов,
# поэтому «Код агента» и «код  агента» распознаются одинаково.
# Для каждой колонки перечислены варианты написания, которые встречались.
COLUMN_NAMES = {
    "supervisor": ("код супервайзера", "супервайзер", "код супервайзер",
                   "код св", "supervisor"),
    "agent":      ("код агента", "агент", "код тп", "agent"),
    "role":       ("роль", "role", "должность"),
    "territory":  ("территория", "регион", "territory"),
}

# Как пишут роль в файле. Сравнение по началу слова: «Супервайзер»,
# «супервайзер(СВ)», «Агент/ТП» — всё распознаётся.
ROLE_SUPERVISOR = ("супервайзер", "суперв", "св", "supervisor")
ROLE_AGENT = ("агент", "тп", "agent")

# Код считается кодом: две и больше букв, затем цифры (UL0101, ULS0101,
# ULSTP0101). Пробелы и дефисы внутри убираются — в файлах они попадаются.
CODE_RE = re.compile(r"^[A-Z]{2,}\d+$")


def clean_code(value) -> str:
    """'  ul 0101 ' → 'UL0101'. Пустое значение → ''."""
    if value is None:
        return ""
    text = str(value).strip().upper()
    text = re.sub(r"[\s\-_.]+", "", text)
    return text


def clean_text(value) -> str:
    return "" if value is None else str(value).strip()


def role_of(value: str) -> str:
    """'Супервайзер' → 'supervisor', 'Агент' → 'agent', иначе ''."""
    text = clean_text(value).lower().replace("ё", "е")
    if not text:
        return ""
    if text.startswith(ROLE_SUPERVISOR):
        return "supervisor"
    if text.startswith(ROLE_AGENT):
        return "agent"
    return ""


def find_header(ws):
    """
    Ищет строку шапки и раскладку колонок.

    Шапка — первая строка, где нашлись И «код супервайзера», И «код агента».
    Искать по номеру строки нельзя: сверху бывает заголовок таблицы
    («LEVER — структура...»), и тогда шапка оказывается второй.
    """
    for row in range(1, min(HEADER_SCAN_ROWS, ws.max_row) + 1):
        found = {}
        for col in range(1, ws.max_column + 1):
            title = clean_text(ws.cell(row, col).value).lower().replace("ё", "е")
            title = re.sub(r"\s+", " ", title)
            if not title:
                continue
            for key, variants in COLUMN_NAMES.items():
                if key in found:
                    continue
                if title in variants or any(title.startswith(v) for v in variants):
                    found[key] = col
                    break
        if "supervisor" in found and "agent" in found:
            return row, found
    return None, {}


def read_sheet(ws):
    """
    Разбирает один лист.

    Возвращает:
      supervisors — {код: территория} для строк с ролью «Супервайзер»
      agents      — [(код агента, код супервайзера, территория)]
      issues      — замечания по этому листу
    """
    header_row, cols = find_header(ws)
    if header_row is None:
        return {}, [], [(ws.title, "", "Шапка не найдена",
                         "нет колонок «Код супервайзера» и «Код агента»")]

    supervisors, agents, issues = {}, [], []
    seen_agents = {}

    for row in range(header_row + 1, ws.max_row + 1):
        sv = clean_code(ws.cell(row, cols["supervisor"]).value)
        ag = clean_code(ws.cell(row, cols["agent"]).value)
        terr = (clean_text(ws.cell(row, cols["territory"]).value)
                if "territory" in cols else "")
        role = (role_of(ws.cell(row, cols["role"]).value)
                if "role" in cols else "")

        if not sv and not ag:
            continue  # пустая строка-разделитель

        # Роль в файле — главный источник. Если колонки роли нет или она
        # пустая, различаем по тому, заполнен ли код агента.
        if not role:
            role = "agent" if ag else "supervisor"

        if role == "supervisor":
            if not sv:
                issues.append((ws.title, f"строка {row}", "Пустой код",
                               "роль «Супервайзер», но код супервайзера пуст"))
                continue
            if not CODE_RE.match(sv):
                issues.append((ws.title, sv, "Странный код",
                               "не похоже на код супервайзера"))
            if sv in supervisors:
                issues.append((ws.title, sv, "Дубль супервайзера",
                               "встречается на листе дважды"))
            supervisors[sv] = terr
            if ag:
                issues.append((ws.title, sv, "Лишний код агента",
                               f"роль «Супервайзер», но заполнен и код агента {ag}"))
            continue

        # Строка агента
        if not ag:
            issues.append((ws.title, f"строка {row}", "Пустой код",
                           "роль «Агент», но код агента пуст"))
            continue
        if not CODE_RE.match(ag):
            issues.append((ws.title, ag, "Странный код",
                           "не похоже на код агента"))
        if not sv:
            issues.append((ws.title, ag, "Агент без супервайзера",
                           "код супервайзера в строке пуст"))
            continue

        if ag in seen_agents and seen_agents[ag] != sv:
            issues.append((ws.title, ag, "Агент у двух супервайзеров",
                           f"на листе: {seen_agents[ag]} и {sv}"))
        seen_agents[ag] = sv
        agents.append((ag, sv, terr))

    return supervisors, agents, issues


def read_file(path):
    """Читает всю книгу: каждый лист — отдельный бренд."""
    book = openpyxl.load_workbook(path, data_only=True, read_only=True)

    supervisors = {}        # код -> {"territory":..., "sheet":..., "declared": bool}
    links = {}              # код агента -> (код супервайзера, территория, лист)
    issues = []
    summary = []

    for ws in book.worksheets:
        sheet_sv, sheet_ag, sheet_issues = read_sheet(ws)
        issues.extend(sheet_issues)

        for sv, terr in sheet_sv.items():
            if sv in supervisors:
                issues.append((ws.title, sv, "Супервайзер на двух листах",
                               f"уже был на листе {supervisors[sv]['sheet']}"))
            supervisors[sv] = {"territory": terr, "sheet": ws.title,
                               "declared": True}

        for ag, sv, terr in sheet_ag:
            # Повтор внутри одного листа уже записан в read_sheet —
            # второй раз про него писать незачем
            if ag in links and links[ag][0] != sv and links[ag][2] != ws.title:
                issues.append((ws.title, ag, "Агент на двух листах",
                               f"был на листе {links[ag][2]} "
                               f"у супервайзера {links[ag][0]}"))
            links[ag] = (sv, terr, ws.title)

            # Супервайзер, на которого ссылается агент, но которого нет
            # отдельной строкой. Учётку ему всё равно заведём — иначе агент
            # пропадёт из панели, — но в отчёт это попадёт.
            if sv not in supervisors:
                supervisors[sv] = {"territory": terr, "sheet": ws.title,
                                   "declared": False}
                issues.append((ws.title, sv, "Супервайзер не объявлен",
                               f"строки с ролью «Супервайзер» нет, "
                               f"но на него ссылается агент {ag}"))

        # Бренд берём из кода (первые две буквы) — так же, как его считает
        # бот. Если на одном листе коды разных брендов, это почти всегда
        # ошибка в файле, и её лучше увидеть сразу.
        brands = Counter(code[:2] for code in
                         list(sheet_sv) + [a for a, _, _ in sheet_ag] if code)
        if len(brands) > 1:
            main_brand = brands.most_common(1)[0][0]
            for code in list(sheet_sv) + [a for a, _, _ in sheet_ag]:
                if code[:2] != main_brand:
                    issues.append((ws.title, code, "Чужой бренд на листе",
                                   f"{code[:2]} против {main_brand} у остальных"))

        summary.append((ws.title,
                        brands.most_common(1)[0][0] if brands else "—",
                        len(sheet_sv), len(sheet_ag)))

    book.close()

    # Супервайзеры без единого агента
    with_agents = {sv for sv, _, _ in
                   [(v[0], k, v[1]) for k, v in links.items()]}
    for sv, info in supervisors.items():
        if sv not in with_agents:
            issues.append((info["sheet"], sv, "Супервайзер без агентов",
                           "ни одного агента в файле"))

    return supervisors, links, issues, summary


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv

    if not args:
        print('Использование: python migrate_supervisors.py "<строка подключения>" [--dry-run]')
        sys.exit(1)

    try:
        supervisors, links, issues, summary = read_file(SRC)
    except FileNotFoundError:
        print(f"❌ Файл {SRC} не найден — положите его рядом со скриптом.")
        sys.exit(1)

    print(f"{'лист':14} {'бренд':>6} {'суперв.':>9} {'агентов':>9}")
    for sheet, brand, n_sv, n_ag in summary:
        print(f"{sheet:14} {brand:>6} {n_sv:>9} {n_ag:>9}")

    declared = sum(1 for v in supervisors.values() if v["declared"])
    print(f"\nСупервайзеров: {len(supervisors)}"
          + (f" (из них не объявлены строкой: {len(supervisors) - declared})"
             if declared != len(supervisors) else ""))
    print(f"Связок агент → супервайзер: {len(links)}")

    if dry_run:
        print("\n⚠️  Режим --dry-run: в базу ничего не записано.")
        write_report(issues)
        return

    print("\nПодключаюсь к Postgres...")
    try:
        conn = psycopg2.connect(args[0], connect_timeout=15)
    except psycopg2.OperationalError as e:
        print(f"❌ Не удалось подключиться: {e}")
        print("   Строку подключения бери в Railway → Postgres → Variables "
              "→ DATABASE_PUBLIC_URL")
        sys.exit(1)

    cur = conn.cursor()
    cur.execute("""
        SELECT column_name FROM information_schema.columns
         WHERE table_name = 'users' AND column_name IN ('supervisor', 'territory')
    """)
    have = {r[0] for r in cur.fetchall()}

    if "supervisor" not in have:
        print("\n❌ В таблице users нет колонки supervisor.")
        print("   Сначала выполни supervisor_setup.sql.")
        conn.close()
        sys.exit(1)

    save_territory = "territory" in have
    if not save_territory:
        print("ℹ️  Колонки users.territory нет — территория не сохранится.")
        print("   Чтобы сохранялась, выполни:")
        print("   ALTER TABLE users ADD COLUMN IF NOT EXISTS territory TEXT;")

    # ---- учётки супервайзеров ----
    # Бренд супервайзера — первые две буквы его кода, как и у агентов:
    # ULS0101 и UL0101 — один бренд UL.
    sv_rows = [(sv.lower(), DEFAULT_PASSWORD, "supervisor", sv[:2],
                info["territory"] or None)
               for sv, info in sorted(supervisors.items())]

    if save_territory:
        execute_values(cur, """
            INSERT INTO users (login, password, role, brand, territory)
            VALUES %s
            ON CONFLICT (login) DO UPDATE
                SET role      = 'supervisor',
                    brand     = EXCLUDED.brand,
                    territory = EXCLUDED.territory
        """, sv_rows)
    else:
        execute_values(cur, """
            INSERT INTO users (login, password, role, brand)
            VALUES %s
            ON CONFLICT (login) DO UPDATE
                SET role  = 'supervisor',
                    brand = EXCLUDED.brand
        """, [r[:4] for r in sv_rows])

    # ---- агенты: заводим отсутствующих и проставляем супервайзера ----
    # Роль здесь НЕ перезаписывается: если человек в базе уже оператор или
    # супервайзер, строка из файла не должна разжаловать его в агенты.
    ag_rows = [(ag.lower(), DEFAULT_PASSWORD, "agent", ag[:2], sv,
                terr or None)
               for ag, (sv, terr, _) in sorted(links.items())]

    if save_territory:
        execute_values(cur, """
            INSERT INTO users (login, password, role, brand, supervisor, territory)
            VALUES %s
            ON CONFLICT (login) DO UPDATE
                SET supervisor = EXCLUDED.supervisor,
                    brand      = EXCLUDED.brand,
                    territory  = EXCLUDED.territory
        """, ag_rows)
    else:
        execute_values(cur, """
            INSERT INTO users (login, password, role, brand, supervisor)
            VALUES %s
            ON CONFLICT (login) DO UPDATE
                SET supervisor = EXCLUDED.supervisor,
                    brand      = EXCLUDED.brand
        """, [r[:5] for r in ag_rows])

    conn.commit()

    # ---- что в базе, но не в файле ----
    cur.execute("""
        SELECT upper(login), COALESCE(upper(supervisor), ''), COALESCE(role, 'agent')
          FROM users
         ORDER BY login
    """)
    in_db = cur.fetchall()

    cur.execute("SELECT count(*) FROM users WHERE role = 'supervisor'")
    total_sv = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM users WHERE supervisor IS NOT NULL")
    total_ag = cur.fetchone()[0]
    cur.close()
    conn.close()

    for login, sv, role in in_db:
        if role == "agent" and login not in links:
            issues.append(("база", login, "Агента нет в файле",
                           f"в базе остался у {sv}" if sv
                           else "в базе и без супервайзера"))

        # Логин из файла, который в базе уже не агент. Роль мы НЕ меняем:
        # разжаловать живого оператора или супервайзера строкой из файла
        # опаснее, чем оставить расхождение и показать его здесь.
        if role != "agent" and login in links and login not in supervisors:
            issues.append(("база", login, "Роль не совпадает",
                           f"в файле агент, а в базе {role} — роль не менялась"))

    write_report(issues)

    print(f"\n✅ Супервайзеров в базе: {total_sv}")
    print(f"✅ Агентов с супервайзером: {total_ag}")
    print(f"🔑 Пароль новых учёток: {DEFAULT_PASSWORD} — смените после первого входа")


def write_report(issues):
    with open(REPORT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Лист", "Код", "Проблема", "Подробности"])
        w.writerows(issues)

    print(f"\n⚠️  Замечаний: {len(issues)}")
    # Группируем по названию проблемы, а не по тексту с подставленными
    # кодами: иначе каждая строка отчёта была бы отдельным пунктом сводки
    for reason, count in Counter(i[2] for i in issues).most_common():
        print(f"     • {reason}: {count}")
    print(f"\n📄 Подробности: {REPORT}")


if __name__ == "__main__":
    main()
