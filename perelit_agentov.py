"""
Перелив базы агента на другого агента (например, MX0103 -> KL0103).

Что делает для каждой пары ИСТОЧНИК=ПРИЁМНИК:
  1. сохраняет копию текущих прикреплений приёмника (таблица backup_perelit);
  2. удаляет прикрепления приёмника;
  3. копирует прикрепления источника на приёмника (точка, день визита;
     бренд ставится по приёмнику).
Источник не меняется. Всё в одной транзакции: при ошибке откат.

Без --apply ничего не пишет, только показывает, что изменится.

    python perelit_agentov.py --db "postgresql://..." MX0103=KL0103 MX0104=KL0104
    python perelit_agentov.py --db "..." MX0103=KL0103 --apply

Откат (вернуть прежнее состояние приёмников):
    python perelit_agentov.py --db "..." --rollback НОМЕР_ПЕРЕЛИВА
"""
import argparse, asyncio, os, sys
import asyncpg


def pairs_from(args):
    out = []
    for a in args:
        if "=" not in a:
            sys.exit(f"Пара должна быть вида ИСТОЧНИК=ПРИЁМНИК, а не «{a}»")
        s, d = [x.strip().upper() for x in a.split("=", 1)]
        if not s or not d or s == d:
            sys.exit(f"Неверная пара «{a}»")
        out.append((s, d))
    dst = [d for _, d in out]
    if len(set(dst)) != len(dst):
        sys.exit("Один приёмник указан дважды")
    return out


async def show(conn, pairs):
    print(f"{'источник':<10}{'точек/дней':>12}   {'приёмник':<10}{'сейчас':>8}   в users")
    bad = False
    for s, d in pairs:
        ns = await conn.fetchval("SELECT count(*) FROM attachments WHERE upper(agent)=$1", s)
        nd = await conn.fetchval("SELECT count(*) FROM attachments WHERE upper(agent)=$1", d)
        u = await conn.fetchval("SELECT count(*) FROM users WHERE upper(login)=$1", d)
        us = await conn.fetchval("SELECT count(*) FROM users WHERE upper(login)=$1", s)
        flag = "да" if u else "НЕТ такого агента"
        if not u or not us or ns == 0:
            bad = True
            if not us: flag += "; источника нет в users"
            if ns == 0: flag += "; у источника нет точек"
        print(f"{s:<10}{ns:>12}   {d:<10}{nd:>8}   {flag}")
    return bad


async def apply(conn, pairs):
    async with conn.transaction():
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS backup_perelit (
                run_id int, saved_at timestamptz DEFAULT now(),
                src text, dst text,
                point_code text, point_name text, agent_brand text,
                agent text, visit_day text, created_at timestamptz)""")
        run = await conn.fetchval("SELECT COALESCE(max(run_id),0)+1 FROM backup_perelit")
        for s, d in pairs:
            await conn.execute("""
                INSERT INTO backup_perelit (run_id, src, dst, point_code, point_name,
                       agent_brand, agent, visit_day, created_at)
                SELECT $1,$2,$3, point_code, point_name, agent_brand, agent, visit_day, created_at
                  FROM attachments WHERE upper(agent)=$3""", run, s, d)
            deleted = await conn.fetchval(
                "WITH x AS (DELETE FROM attachments WHERE upper(agent)=$1 RETURNING 1) SELECT count(*) FROM x", d)
            added = await conn.fetchval("""
                WITH x AS (
                  INSERT INTO attachments (point_code, point_name, agent_brand, agent, visit_day)
                  SELECT DISTINCT point_code, point_name, left($2,2), $2, visit_day
                    FROM attachments WHERE upper(agent)=$1
                  ON CONFLICT DO NOTHING RETURNING 1)
                SELECT count(*) FROM x""", s, d)
            await conn.execute("""
                INSERT INTO transfer_log (point_code, point_name, from_agent, to_agent, days, by_login)
                VALUES ('—','перелив базы агента',$1,$2,$3,'PERELIT')""",
                s, d, f"перелив: удалено {deleted}, добавлено {added}")
            print(f"{s} -> {d}: удалено {deleted}, добавлено {added}")
    print(f"\nГотово. Номер перелива для отката: {run}")


async def rollback(conn, run):
    async with conn.transaction():
        rows = await conn.fetch("SELECT DISTINCT dst FROM backup_perelit WHERE run_id=$1", run)
        if not rows:
            sys.exit(f"Перелива №{run} нет")
        for r in rows:
            d = r["dst"]
            await conn.execute("DELETE FROM attachments WHERE upper(agent)=$1", d)
            await conn.execute("""
                INSERT INTO attachments (point_code, point_name, agent_brand, agent, visit_day, created_at)
                SELECT point_code, point_name, agent_brand, agent, visit_day, created_at
                  FROM backup_perelit WHERE run_id=$1 AND dst=$2
                ON CONFLICT DO NOTHING""", run, d)
            print(f"{d}: возвращено прежнее состояние")


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pairs", nargs="*")
    ap.add_argument("--db", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", type=int)
    a = ap.parse_args()
    if not a.db: sys.exit('Укажите --db "postgresql://..."')
    conn = await asyncpg.connect(a.db, timeout=20)
    try:
        if a.rollback:
            return await rollback(conn, a.rollback)
        if not a.pairs: sys.exit("Укажите пары, например MX0103=KL0103")
        pairs = pairs_from(a.pairs)
        bad = await show(conn, pairs)
        if not a.apply:
            print("\nЭто просмотр, в базе ничего не изменено. Для выполнения добавьте --apply")
            return
        if bad:
            sys.exit("\nСтоп: есть замечания выше. Исправьте пары и запустите снова.")
        print()
        await apply(conn, pairs)
    finally:
        await conn.close()

asyncio.run(main())
