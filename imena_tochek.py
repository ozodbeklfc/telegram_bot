"""
Точки без названия: найти и заполнить.

Названия берутся по порядку из:
  1. актуальной клиентской базы (--base): код | название | статус | ИНН;
  2. самой базы: у той же точки в attachments название есть, а в client_base пусто;
  3. файла CRM (--crm файл.xlsx или .csv) по коду точки.
Остальные скрипт перечисляет в файле bez_nazvaniya.csv, ничего не придумывая.
Пустые названия заменяются только на непустые, заполненные не трогаются.

    python imena_tochek.py --db "postgresql://..."                  (просмотр)
    python imena_tochek.py --db "..." --base baza.xlsx              (по клиентской базе)
    python imena_tochek.py --db "..." --crm crm.xlsx                (просмотр + CRM)
    python imena_tochek.py --db "..." --crm crm.xlsx --apply        (записать)

Заодно скрипт показывает ТОП-точки, у которых дней больше трёх
(в файле top_bolshe_3_dney.csv): их он НЕ меняет, какой день убрать,
решает супервайзер.
"""
import argparse, asyncio, csv, os, sys
import asyncpg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EMPTY = "coalesce(trim(point_name),'') IN ('', '-', '—')"


def load_crm(path):
    try:
        from dobavit_iz_crm import read_any, clean_code
    except ImportError:
        sys.exit("Для --crm рядом должен лежать dobavit_iz_crm.py")
    _, names = read_any(path)
    return {clean_code(k): v for k, v in names.items() if str(v).strip()}, clean_code


def say(*a):
    print(*a, flush=True)


def load_base(path):
    """Клиентская база: код точки | название | статус | ИНН (шапка не нужна).
    Колонки узнаются по содержимому: код вида 120.01.101.0213, ИНН из цифр."""
    import re
    code_re = re.compile(r"^\d[\d.]{4,}$")
    rows = []
    if path.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl
        say("Читаю файл (большой xlsx открывается 1-2 минуты, это нормально)...")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for ws in wb.worksheets:
            for k, r in enumerate(ws.iter_rows(values_only=True), 1):
                rows.append(list(r))
                if k % 5000 == 0:
                    say(f"   прочитано строк: {k}")
    else:
        for enc in ("utf-8-sig", "cp1251"):
            try:
                text = open(path, encoding=enc).read()
                break
            except UnicodeDecodeError:
                continue
        sample = text[:2000]
        delim = max(";,\t", key=sample.count)
        rows = list(csv.reader(text.splitlines(), delimiter=delim))
    names, inns = {}, {}
    for r in rows:
        cells = [("" if c is None else str(c).strip()) for c in r]
        code = next((c for c in cells if code_re.match(c) and c.count(".") >= 2), "")
        if not code:
            continue
        i = cells.index(code)
        rest = [c for c in cells[i + 1:] if c]
        # название: первая ячейка с буквами; ИНН: первая из 9 и более цифр
        name = next((c for c in rest if re.search(r"[A-Za-zА-Яа-яЁё]", c)), "")
        inn = next((re.sub(r"\.0$", "", c) for c in rest if re.fullmatch(r"\d{9,14}(\.0)?", c)), "")
        if name:
            names.setdefault(code, name)
        if inn:
            inns.setdefault(code, inn)
    return names, inns


def save_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(header)
        w.writerows(rows)


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--crm")
    ap.add_argument("--base", help="актуальная клиентская база: код | название | статус | ИНН")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--add-missing", action="store_true",
                    help="точки, которых нет в client_base, создать из клиентской базы (с --base)")
    ap.add_argument("--code", help="показать, что скрипт знает про одну точку (диагностика)")
    a = ap.parse_args()
    if not a.db:
        sys.exit('Укажите --db "postgresql://..."')

    conn = None
    for attempt in (1, 2, 3):
        say(f"Подключаюсь к базе (попытка {attempt} из 3, до 30 с)...")
        try:
            conn = await asyncpg.connect(a.db, timeout=30)
            break
        except (asyncio.TimeoutError, TimeoutError, OSError) as e:
            say(f"   не вышло: {type(e).__name__}")
    if conn is None:
        sys.exit("Не удалось подключиться за три попытки. Проверьте адрес базы и интернет, "
                 "попробуйте позже или с другой сети (например, раздача с телефона).")
    say("Подключился.")
    try:
        if a.code:
            if not a.base:
                sys.exit("С --code нужен --base файл")
            bn, bi = load_base(a.base)
            row = await conn.fetchrow(
                "SELECT point_code, point_name, inn FROM client_base WHERE point_code=$1", a.code.strip())
            say(f"\nВ файле:  название={bn.get(a.code.strip())!r}  ИНН={bi.get(a.code.strip())!r}")
            say(f"В базе:   {dict(row) if row else 'такой точки нет'}")
            return
        say("Ищу точки без названия...")
        # Точки без названия в справочнике. Берём и те, что есть только в
        # attachments (в client_base строки нет совсем)
        rows = await conn.fetch(f"""
            SELECT c.point_code, c.inn, true AS in_base,
                   (SELECT count(*) FROM attachments t WHERE t.point_code = c.point_code) AS prikr
              FROM client_base c WHERE {EMPTY.replace('point_name', 'c.point_name')}
            UNION ALL
            SELECT t.point_code, NULL, false, count(*)
              FROM attachments t
             WHERE NOT EXISTS (SELECT 1 FROM client_base c WHERE c.point_code = t.point_code)
             GROUP BY t.point_code""")
        print(f"Точек без названия: {len(rows)} "
              f"(из них вовсе нет в client_base: {sum(1 for r in rows if not r['in_base'])})")
        if not rows:
            print("Всё в порядке.")
        # 1. название из attachments
        from_att = {r["point_code"]: r["n"] for r in await conn.fetch(f"""
            SELECT point_code, max(trim(point_name)) AS n FROM attachments
             WHERE NOT ({EMPTY}) GROUP BY point_code""")}

        crm, clean = ({}, lambda x: x)
        if a.crm:
            crm, clean = load_crm(a.crm)

        base_names, base_inns = ({}, {})
        if a.base:
            base_names, base_inns = load_base(a.base)
            print(f"Клиентская база: {len(base_names)} названий, {len(base_inns)} ИНН")

        fill, left = [], []
        for r in rows:
            code = r["point_code"]
            name, src = base_names.get(code), "клиентская база"
            if not name:
                name, src = from_att.get(code), "attachments"
            if not name:
                name, src = crm.get(clean(code)), "CRM"
            if name and r["in_base"]:
                fill.append((code, name, src))
            else:
                left.append((code, r["inn"] or "", "нет в client_base" if not r["in_base"] else "", r["prikr"]))
        to_add = []
        if a.add_missing:
            if not a.base:
                sys.exit("--add-missing работает только вместе с --base")
            for r in rows:
                if not r["in_base"] and base_names.get(r["point_code"]):
                    to_add.append((r["point_code"], base_names[r["point_code"]],
                                   base_inns.get(r["point_code"], "")))
            left = [l for l in left if l[0] not in {t[0] for t in to_add}]
            print(f"Нет в client_base, но есть в клиентской базе (будут созданы): {len(to_add)}")
        print(f"Можно заполнить: {len(fill)} "
              f"(из attachments {sum(1 for f in fill if f[2]=='attachments')}, "
              f"из клиентской базы {sum(1 for f in fill if f[2]=='клиентская база')}, из CRM {sum(1 for f in fill if f[2]=='CRM')})")
        print(f"Останутся без названия: {len(left)}")
        for f in fill[:8]:
            print(f"   {f[0]}  ->  {f[1]}   [{f[2]}]")
        if left:
            save_csv("bez_nazvaniya.csv", ["код точки", "ИНН", "примечание", "прикреплений"], left)
            print("Список без названия: bez_nazvaniya.csv")

        # ТОП-точки с лишними днями (только отчёт)
        top = await conn.fetch("""
            SELECT t.point_code, COALESCE(NULLIF(trim(c.point_name),''), '—') AS name,
                   upper(t.agent) AS agent, count(DISTINCT t.visit_day) AS days,
                   string_agg(DISTINCT t.visit_day, ', ') AS dd
              FROM attachments t JOIN client_base c ON c.point_code = t.point_code
             WHERE c.is_top
             GROUP BY t.point_code, c.point_name, upper(t.agent)
            HAVING count(DISTINCT t.visit_day) > 3
             ORDER BY 3, 1""")
        print(f"\nТОП-точек с днями больше трёх: {len(top)}")
        if top:
            save_csv("top_bolshe_3_dney.csv", ["код", "название", "агент", "дней", "дни"],
                     [(r["point_code"], r["name"], r["agent"], r["days"], r["dd"]) for r in top])
            print("Список: top_bolshe_3_dney.csv (их не меняю — день выбирает супервайзер)")

        if not a.apply:
            print("\n!!! ЭТО ТОЛЬКО ПРОСМОТР: в базе ничего не изменено. Чтобы записать, добавьте в конец команды --apply")
            return
        if not fill and not to_add:
            return
        async with conn.transaction():
            if to_add:
                await conn.execute("""
                    INSERT INTO client_base (point_code, point_name, inn, status, type, is_top)
                    SELECT v.code, v.n, v.i, 0, 'def', false
                      FROM unnest($1::text[], $2::text[], $3::text[]) AS v(code, n, i)
                     WHERE NOT EXISTS (SELECT 1 FROM client_base c WHERE c.point_code = v.code)""",
                    [t[0] for t in to_add], [t[1] for t in to_add], [t[2] for t in to_add])
            if to_add:
                print(f"Создано точек в client_base: {len(to_add)}")
            # Всё пачками: тысячи отдельных запросов по интернету идут часами
            if fill:
                codes = [f[0] for f in fill]
                names = [f[1] for f in fill]
                res = await conn.execute(f"""
                    UPDATE client_base c SET point_name = v.n
                      FROM unnest($1::text[], $2::text[]) AS v(code, n)
                     WHERE c.point_code = v.code AND {EMPTY.replace('point_name', 'c.point_name')}""",
                    codes, names)
                n = int(res.split()[-1])
            else:
                n = 0
            say(f"Названия записаны: {n}")
            ni = 0
            if base_inns:
                res = await conn.execute("""
                    UPDATE client_base c SET inn = v.i
                      FROM unnest($1::text[], $2::text[]) AS v(code, i)
                     WHERE c.point_code = v.code AND coalesce(trim(c.inn),'') = ''""",
                    list(base_inns.keys()), list(base_inns.values()))
                ni = int(res.split()[-1])
                say(f"Пустых ИНН заполнено: {ni}")
            # в attachments подправим пустые названия у тех же точек
            m = await conn.execute(f"""
                UPDATE attachments t SET point_name = c.point_name
                  FROM client_base c
                 WHERE c.point_code = t.point_code
                   AND {EMPTY.replace('point_name', 't.point_name')}
                   AND coalesce(trim(c.point_name),'') <> ''""")
        print(f"\nЗаписано: client_base {n}, attachments {m.split()[-1]}")
        left_now = await conn.fetchval(f"SELECT count(*) FROM client_base WHERE {EMPTY}")
        say(f"Пустых названий в client_base теперь: {left_now}")
    finally:
        await conn.close()

if sys.platform == "win32":
    # На Windows стандартный режим asyncio иногда зависает на SSL-рукопожатии
    # с прокси Railway; этот режим работает стабильнее
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
asyncio.run(main())
