"""
Работа с Postgres напрямую (без Google Apps Script).

Интерфейс функций (login / check_inn / attach / add_tt) специально оставлен
прежним: те же аргументы, те же ключи в ответе. Поэтому bot.py менять
из-за перехода на базу не пришлось.

Все функции возвращают словарь и НИКОГДА не бросают исключение наружу —
при проблеме с базой вернётся {"success": False, "message": "..."},
которое bot.py уже умеет показывать пользователю.
"""

import json
import logging
import re
import secrets

import asyncpg

import notify

from config import DATABASE_URL
# Рабочие дни недели берём из того же справочника, что и бот: если список
# когда-нибудь изменится (появится суббота), правило пересчёта визитов
# не должно остаться с прежними пятью днями.
from data import DAYS as WORK_DAYS

logger = logging.getLogger(__name__)

# Порог схожести названий (0..1). 0.35 ловит "MUXAYYO TRADE" против
# "MUHAYO TRADE" (сходство 0.5) и при этом не выдаёт случайные совпадения.
# Снизишь — будет больше ложных срабатываний, повысишь — опечатки начнут
# проскакивать мимо.
SIMILARITY_THRESHOLD = 0.35
SIMILAR_LIMIT = 3

POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 10
COMMAND_TIMEOUT = 15  # секунд на один запрос

_pool: asyncpg.Pool | None = None


async def _init_connection(conn):
    """
    Выполняется для каждого нового соединения в пуле.

    pg_trgm сравнивает строки оператором %, а порог срабатывания хранится
    в настройке соединения. Без этой строки порог был бы 0.3 по умолчанию,
    и он бы не совпадал с SIMILARITY_THRESHOLD, по которому мы потом
    отсеиваем результаты.
    """
    await conn.execute(f"SET pg_trgm.similarity_threshold = {SIMILARITY_THRESHOLD}")


async def get_pool() -> asyncpg.Pool:
    """
    Пул соединений создаётся один раз при первом обращении и живёт до
    остановки бота. Открывать соединение на каждый запрос — дорого:
    это TCP + TLS + аутентификация каждый раз.
    """
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=POOL_MIN_SIZE,
            max_size=POOL_MAX_SIZE,
            command_timeout=COMMAND_TIMEOUT,
            setup=_init_connection,
        )
        logger.info("Пул соединений с Postgres создан")
    return _pool


async def close_pool():
    """Вызывается при остановке бота (в finally у main в bot.py)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Пул соединений с Postgres закрыт")


def _db_error(e: Exception) -> dict:
    logger.exception("Ошибка при обращении к базе данных")
    return {
        "success": False,
        "message": f"База данных сейчас не отвечает ({e}). Попробуйте ещё раз через минуту.",
    }


# ======================================================================
# АВТОРИЗАЦИЯ
# ======================================================================

async def login(login_value: str, password: str) -> dict:
    login_value = (login_value or "").strip().lower()
    password = (password or "").strip()

    if not login_value or not password:
        return {"success": False, "message": "Заполните логин и пароль"}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT login,
                       COALESCE(role, 'agent') AS role,
                       -- У агента бренд всегда есть: если колонка пустая,
                       -- берём первые две буквы логина. У админа и оператора
                       -- пустой бренд означает «все бренды», поэтому
                       -- подставлять туда буквы логина нельзя: иначе
                       -- у логина operator появился бы «бренд OP».
                       CASE WHEN COALESCE(role, 'agent') IN ('admin', 'operator')
                            THEN brand
                            ELSE COALESCE(brand, upper(left(login, 2)))
                       END AS brand,
                       supervisor
                  FROM users
                 WHERE login = $1 AND password = $2
                """,
                login_value,
                password,
            )
    except Exception as e:
        return _db_error(e)

    if row is None:
        return {"success": False, "message": "Неверный логин или пароль"}

    return {
        "success": True,
        "message": "Успешный вход!",
        "role": row["role"],
        "brand": row["brand"],
        "supervisor": row["supervisor"],
        "user": {"login": row["login"], "role": row["role"], "brand": row["brand"]},
    }


async def change_password(login_value: str, current_password: str, new_password: str) -> dict:
    """
    Текущий пароль проверяется прямо в UPDATE (условие в WHERE), а не отдельным
    SELECT'ом: так между проверкой и записью не остаётся промежутка, и всё
    делается одним обращением к базе.

    RETURNING login возвращает строку только если UPDATE реально что-то изменил.
    Если пришло None — либо логина нет, либо текущий пароль не совпал.
    Пользователю не уточняем, что именно: это подсказка для перебора.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            updated = await conn.fetchval(
                """
                UPDATE users
                   SET password = $3
                 WHERE login = $1
                   AND password = $2
             RETURNING login
                """,
                (login_value or "").strip().lower(),
                current_password,
                new_password,
            )
    except Exception as e:
        return _db_error(e)

    if updated is None:
        return {"success": False, "message": "Текущий пароль неверный"}

    return {"success": True, "message": "Пароль изменён"}


# ======================================================================
# АДМИН-ПАНЕЛЬ
# ======================================================================

async def list_brands() -> dict:
    """
    Бренды, которые есть в базе, со счётчиками.

    Нужен общему админу: он входит одним логином и первым шагом выбирает,
    чей бренд смотреть.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH b AS (
                    SELECT COALESCE(brand, upper(left(login, 2))) AS brand,
                           upper(login) AS agent
                      FROM users
                     WHERE COALESCE(role, 'agent') = 'agent'
                    UNION
                    SELECT upper(left(agent, 2)), upper(agent)
                      FROM attachments
                     WHERE agent IS NOT NULL AND agent <> ''
                )
                SELECT b.brand,
                       count(DISTINCT b.agent)        AS agents,
                       count(DISTINCT t.point_code)   AS points
                  FROM b
             LEFT JOIN attachments t ON upper(t.agent) = b.agent
                 WHERE b.brand IS NOT NULL AND b.brand <> ''
                 GROUP BY b.brand
                 ORDER BY agents DESC, b.brand
                """
            )
    except Exception as e:
        return _db_error(e)

    return {
        "success": True,
        "brands": [
            {"brand": r["brand"], "agents": r["agents"], "points": r["points"]}
            for r in rows
        ],
    }


async def list_supervisors(search: str = "") -> dict:
    """Все супервайзеры со сводкой — для общего входа."""
    pattern = f"%{(search or '').strip().upper()}%"
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT upper(u.login)                    AS supervisor,
                       u.brand                           AS brand,
                       count(DISTINCT a.login)           AS agents
                  FROM users u
             LEFT JOIN users a ON upper(a.supervisor) = upper(u.login)
                 WHERE u.role = 'supervisor'
                   AND upper(u.login) LIKE $1
                 GROUP BY u.login, u.brand
                 ORDER BY u.brand, u.login
                """,
                pattern,
            )
    except Exception as e:
        return _db_error(e)

    return {"success": True, "supervisors": [
        {"supervisor": r["supervisor"], "brand": r["brand"], "agents": r["agents"]}
        for r in rows
    ]}


async def list_agents(supervisor: str, search: str = "") -> dict:
    """
    Агенты одного супервайзера со сводкой: сколько точек и сколько
    из них с проблемами. Проблемные точки показываются первыми, поэтому
    их счётчик нужен уже в списке агентов.
    """
    supervisor = (supervisor or "").strip().upper()
    if not supervisor:
        return {"success": False, "message": "Не указан супервайзер"}

    pattern = f"%{(search or '').strip().upper()}%"

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH my_agents AS (
                    SELECT upper(login) AS agent
                      FROM users
                     WHERE upper(supervisor) = $1
                ),
                -- у агента больше трёх дней на одной точке
                too_many AS (
                    SELECT upper(agent) AS agent, point_code
                      FROM attachments
                     WHERE upper(agent) IN (SELECT agent FROM my_agents)
                     GROUP BY 1, 2
                    HAVING count(*) > 3
                ),
                -- на точке несколько агентов ТОГО ЖЕ бренда, что и наш агент
                -- (чужие бренды к его проблемам отношения не имеют,
                --  сетевые точки не считаются)
                same_brand AS (
                    SELECT a.point_code, upper(left(a.agent, 2)) AS brand
                      FROM attachments a
                 LEFT JOIN client_base c ON c.point_code = a.point_code
                     WHERE COALESCE(c.type, 'def') <> 'chain'
                       AND a.agent IS NOT NULL
                     GROUP BY a.point_code, upper(left(a.agent, 2))
                    HAVING count(DISTINCT upper(a.agent)) > 1
                )
                SELECT m.agent,
                       count(DISTINCT t.point_code) AS points,
                       count(DISTINCT t.point_code) FILTER (
                           WHERE (t.point_code, left(m.agent, 2)) IN
                                 (SELECT point_code, brand FROM same_brand)
                              OR (m.agent, t.point_code) IN
                                 (SELECT agent, point_code FROM too_many)
                       ) AS problems
                  FROM my_agents m
             LEFT JOIN attachments t ON upper(t.agent) = m.agent
                 WHERE m.agent LIKE $2
                 GROUP BY m.agent
                 ORDER BY problems DESC, points DESC, m.agent
                """,
                supervisor, pattern,
            )
    except Exception as e:
        return _db_error(e)

    return {
        "success": True,
        "supervisor": supervisor,
        "agents": [
            {"agent": r["agent"], "points": r["points"], "problems": r["problems"]}
            for r in rows
        ],
    }


async def agent_points(agent: str, brand: str = "", search: str = "") -> dict:
    """
    Точки агента с пометкой проблем.

    Проблемы бывают двух видов:
      days  — у агента на точке больше трёх дней визита;
      brand — на точке стоит ещё один агент того же бренда.

    Сетевые точки (type='chain') из второй проверки исключены: там несколько
    агентов одного бренда — норма.

    Проблемные точки идут первыми: ради них панель и открывают.
    """
    agent = (agent or "").strip().upper()
    if not agent:
        return {"success": False, "message": "Не указан агент"}

    like = f"%{search.strip().upper()}%" if (search or "").strip() else ""

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH mine AS (
                    SELECT t.point_code,
                           count(*)                              AS day_count,
                           string_agg(DISTINCT t.visit_day, ', ') AS days,
                           max(t.point_name)                     AS fallback_name
                      FROM attachments t
                     WHERE upper(t.agent) = $1
                     GROUP BY t.point_code
                ),
                conflicts AS (
                    SELECT a.point_code,
                           count(DISTINCT upper(a.agent)) AS agents_same_brand
                      FROM attachments a
                     WHERE a.point_code IN (SELECT point_code FROM mine)
                       AND a.agent IS NOT NULL
                       AND upper(left(a.agent, 2)) = left($1, 2)
                     GROUP BY a.point_code
                )
                SELECT m.point_code,
                       COALESCE(c.point_name, m.fallback_name) AS point_name,
                       c.inn,
                       COALESCE(c.status, 0)      AS status,
                       COALESCE(c.type, 'def')    AS type,
                       COALESCE(c.is_top, false)  AS is_top,
                       m.days,
                       m.day_count,
                       COALESCE(f.agents_same_brand, 1) AS agents_same_brand
                  FROM mine m
             LEFT JOIN client_base c ON c.point_code = m.point_code
             LEFT JOIN conflicts  f ON f.point_code = m.point_code
                 WHERE $2 = ''
                    OR upper(COALESCE(c.point_name, m.fallback_name)) LIKE $2
                    OR m.point_code LIKE $2
                    OR c.inn LIKE $2
                 ORDER BY point_name
                """,
                agent, like,
            )
    except Exception as e:
        return _db_error(e)

    points = []
    for r in rows:
        problems = []
        is_top = bool(r["is_top"])
        # Сколько дней разрешено именно здесь: ТОП — до трёх, обычной — один
        max_here = max_days_for(is_top)

        if r["day_count"] > max_here:
            problems.append("days")
        if r["agents_same_brand"] > 1 and r["type"] != "chain":
            problems.append("brand")

        points.append({
            "pointCode": r["point_code"],
            "pointName": r["point_name"] or "—",
            "inn": r["inn"] or "",
            "status": r["status"],
            "type": r["type"],
            "isTop": is_top,
            "days": r["days"] or "",
            "dayCount": r["day_count"],
            "maxDaysHere": max_here,
            "problems": problems,
        })

    # Проблемные — вверх списка, внутри группы по алфавиту
    points.sort(key=lambda p: (not p["problems"], p["pointName"]))

    # Нагрузка считается по ВСЕЙ базе агента, а не по показанному списку:
    # при поиске в списке остаётся пара точек, а лимит всё равно про все
    load = await agent_load(agent)

    return {
        "success": True,
        "agent": agent,
        "points": points,
        "problemCount": sum(1 for p in points if p["problems"]),
        # Правила и факт для шапки панели
        "pointCount": load.get("pointCount", 0),
        "dayLoad": load.get("dayLoad", {}),
        "workDays": list(WORK_DAYS),
        "weekVisits": load.get("weekVisits", 0),
        "weekCapacity": load.get("weekCapacity", 0),
        "topCount": sum(1 for p in points if p["isTop"]),
        "maxPoints": MAX_POINTS_PER_AGENT,
        "maxVisitsPerDay": MAX_VISITS_PER_DAY,
        "maxDays": MAX_VISIT_DAYS,
        "maxDaysRegular": MAX_VISIT_DAYS_REGULAR,
    }


async def point_details(point_code: str, agent: str = "") -> dict:
    """
    Разбор одной точки: кто на ней стоит, с какими днями и в чём проблема.
    Используется окном, которое открывается по клику на проблемную точку.
    """
    point_code = (point_code or "").strip()
    if not point_code:
        return {"success": False, "message": "Не указана точка"}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            info = await conn.fetchrow(
                """
                SELECT point_name, inn, COALESCE(type, 'def') AS type,
                       COALESCE(status, 0) AS status,
                       COALESCE(is_top, false) AS is_top
                  FROM client_base WHERE point_code = $1
                """,
                point_code,
            )
            rows = await conn.fetch(
                """
                SELECT upper(agent) AS agent,
                       string_agg(DISTINCT visit_day, ', ') AS days,
                       count(*) AS day_count,
                       max(point_name) AS point_name
                  FROM attachments
                 WHERE point_code = $1 AND agent IS NOT NULL
                 GROUP BY upper(agent)
                 ORDER BY 1
                """,
                point_code,
            )
    except Exception as e:
        return _db_error(e)

    point_type = (info["type"] if info else "def")
    is_top = bool(info["is_top"]) if info else False
    # Норма дней зависит от точки: ТОП — до трёх, обычная — один
    max_here = max_days_for(is_top)

    agents = [
        {
            "agent": r["agent"],
            "days": r["days"] or "",
            "dayList": split_days(r["days"]),
            "dayCount": r["day_count"],
            "tooManyDays": r["day_count"] > max_here,
        }
        for r in rows
    ]

    # Всё считаем в рамках бренда открытого агента: агенты других брендов
    # на этой же точке — не его проблема и не должны попадать в окно разбора
    brand = agent_brand(agent) if agent else ""
    same_brand = [a for a in agents if agent_brand(a["agent"]) == brand] if brand else agents

    # Лишние дни — персональная проблема конкретного агента
    over_days = [a for a in (
        [x for x in agents if x["agent"] == agent] if agent else agents
    ) if a["tooManyDays"]]

    problems = []
    if over_days:
        problems.append("days")
    if point_type != "chain" and len(same_brand) > 1:
        problems.append("brand")

    return {
        "success": True,
        "pointCode": point_code,
        "pointName": (info["point_name"] if info else None) or (rows[0]["point_name"] if rows else "—"),
        "inn": (info["inn"] if info else "") or "",
        "type": point_type,
        "isTop": is_top,
        "status": (info["status"] if info else 0),
        "agents": agents,
        "sameBrandAgents": same_brand,
        "overDaysAgents": over_days,
        "problems": problems,
        "maxDays": max_here,
        "maxDaysTop": MAX_VISIT_DAYS,
        "workDays": list(WORK_DAYS),
    }


async def unattach_days(agent: str, point_code: str, days: list) -> dict:
    """Снимает у агента конкретные дни визита на точке."""
    agent = (agent or "").strip().upper()
    days = [d.strip() for d in (days or []) if d and d.strip()]
    if not agent or not point_code or not days:
        return {"success": False, "message": "Не указан агент, точка или дни"}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM attachments
                 WHERE point_code = $1 AND upper(agent) = $2 AND visit_day = ANY($3::text[])
                """,
                point_code, agent, days,
            )
    except Exception as e:
        return _db_error(e)

    removed = int(result.split()[-1]) if result else 0
    return {"success": True, "removed": removed,
            "message": f"Откреплено дней: {removed}"}


async def unattach_agent(agent: str, point_code: str) -> dict:
    """Полностью снимает агента с точки."""
    agent = (agent or "").strip().upper()
    if not agent or not point_code:
        return {"success": False, "message": "Не указан агент или точка"}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM attachments WHERE point_code = $1 AND upper(agent) = $2",
                point_code, agent,
            )
    except Exception as e:
        return _db_error(e)

    removed = int(result.split()[-1]) if result else 0
    return {"success": True, "removed": removed,
            "message": f"{agent} откреплён от точки (снято дней: {removed})"}


async def set_point_type(point_code: str, point_type: str) -> dict:
    """
    Помечает точку сетевой или обычной.

    Сетевая точка снимает проблему «несколько агентов одного бренда»:
    в сети это нормальная ситуация, а не ошибка прикрепления.
    """
    point_type = (point_type or "").strip().lower()
    if point_type not in ("def", "chain"):
        return {"success": False, "message": "Тип точки может быть только def или chain"}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            updated = await conn.fetchval(
                "UPDATE client_base SET type = $2 WHERE point_code = $1 RETURNING point_code",
                point_code, point_type,
            )
    except Exception as e:
        return _db_error(e)

    if updated is None:
        return {"success": False, "message": "Точка не найдена в клиентской базе"}

    return {"success": True, "type": point_type,
            "message": "Точка отмечена как сетевая" if point_type == "chain"
                       else "Точка отмечена как обычная"}


async def set_point_top(point_code: str, is_top: bool) -> dict:
    """
    Помечает точку ТОП или обычной.

    От этого зависит, сколько дней визита на ней можно занять: ТОП — до
    трёх, обычная — ровно один. Отметка отдельная от «сетевой»: точка
    бывает и той, и другой сразу.

    Уже прикреплённые дни при снятии отметки НЕ трогаются: снять лишние —
    осознанное решение супервайзера, и делать это молча за него нельзя.
    Точка просто станет проблемной в списке.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            updated = await conn.fetchval(
                "UPDATE client_base SET is_top = $2 WHERE point_code = $1 RETURNING point_code",
                point_code, bool(is_top),
            )
    except Exception as e:
        return _db_error(e)

    if updated is None:
        return {"success": False, "message": "Точка не найдена в клиентской базе"}

    return {"success": True, "isTop": bool(is_top),
            "message": f"Точка отмечена как ТОП — до {MAX_VISIT_DAYS} дней визита"
                       if is_top else
                       "Точка отмечена как обычная — один день визита в неделю"}


async def transfer_candidates(from_agent: str, point_code: str,
                              supervisor: str = "", any_agent: bool = False) -> dict:
    """
    Кому можно передать точку: агенты того же бренда с запасом до лимита.

    Отдаём вместе с их нагрузкой, чтобы супервайзер выбирал не вслепую:
    сколько у кандидата точек, сколько визитов в каждый день и какие дни
    у него ещё свободны.

    any_agent=True — для общего админа: он видит агентов бренда независимо
    от того, кому они подчиняются. Супервайзер видит только своих.
    """
    from_agent = (from_agent or "").strip().upper()
    brand = agent_brand(from_agent)
    if not brand:
        return {"success": False, "message": "Не указан агент"}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT upper(login) AS agent
                  FROM users
                 WHERE COALESCE(role, 'agent') = 'agent'
                   AND upper(left(login, 2)) = $1
                   AND upper(login) <> $2
                   AND ($3 OR upper(COALESCE(supervisor, '')) = $4)
                 ORDER BY login
                """,
                brand, from_agent, any_agent, (supervisor or "").strip().upper(),
            )
    except Exception as e:
        return _db_error(e)

    candidates = []
    for r in rows:
        load = await agent_load(r["agent"])
        if not load.get("success"):
            continue

        # Отдающего на точке как бы нет: его снимут при передаче
        check = await check_attach_allowed(point_code, r["agent"],
                                           ignore_agent=from_agent)
        candidates.append({
            "agent": r["agent"],
            "pointCount": load["pointCount"],
            "freePoints": max(0, MAX_POINTS_PER_AGENT - load["pointCount"]),
            "dayLoad": load["dayLoad"],
            "fullDays": load["fullDays"],
            "weekVisits": load["weekVisits"],
            # Может ли он вообще взять эту точку и какие дни ему доступны
            "canTake": bool(check.get("allowed")),
            "reason": check.get("reason"),
            "freeDays": check.get("freeDays", []),
            "maxDaysHere": check.get("maxDaysHere", MAX_VISIT_DAYS),
        })

    # Сначала те, кто реально может взять, и у кого больше запаса
    candidates.sort(key=lambda c: (not c["canTake"], -c["freePoints"], c["agent"]))

    return {
        "success": True,
        "brand": brand,
        "fromAgent": from_agent,
        "candidates": candidates,
        "maxPoints": MAX_POINTS_PER_AGENT,
        "maxVisitsPerDay": MAX_VISITS_PER_DAY,
    }


async def transfer_point(point_code: str, from_agent: str, to_agent: str,
                         days: list, by_login: str = "") -> dict:
    """
    Передаёт точку от одного агента другому.

    Делается одной транзакцией: если новые дни не записались, старые
    должны остаться на месте — иначе точка повиснет ничьей, и агент
    просто перестанет на неё ездить, никого не предупредив.

    Дни выбирает супервайзер: у принимающего агента свой маршрут, и дни
    отдающего могут у него не подойти.
    """
    from_agent = (from_agent or "").strip().upper()
    to_agent = (to_agent or "").strip().upper()
    days = [d.strip() for d in (days or []) if d and d.strip()]

    if not point_code or not from_agent or not to_agent:
        return {"success": False, "message": "Не указана точка или агенты"}

    if from_agent == to_agent:
        return {"success": False, "message": "Это тот же самый агент"}

    if not days:
        return {"success": False, "message": "Не выбран ни один день визита"}

    repeated = [d for d in set(days) if days.count(d) > 1]
    if repeated:
        return {"success": False,
                "message": f"День выбран дважды: {', '.join(repeated)}"}

    denied = await _require_agent(to_agent.lower())
    if denied:
        return {"success": False,
                "message": f"{to_agent}: {denied['message']}"}

    if agent_brand(to_agent) != agent_brand(from_agent):
        return {"success": False,
                "message": f"{to_agent} другого бренда — точка обслуживается "
                           f"агентом бренда {agent_brand(from_agent)}"}

    # Те же правила, что и при обычном прикреплении: передача не должна
    # быть дырой, через которую у агента появляется 151-я точка
    check = await check_attach_allowed(point_code, to_agent,
                                       ignore_agent=from_agent)
    if not check.get("success"):
        return check
    if not check.get("allowed"):
        return {"success": False, "message": f"{to_agent}: {attach_denied_text(check)}"}

    max_here = check.get("maxDaysHere", MAX_VISIT_DAYS)
    if len(days) > max_here:
        return {"success": False,
                "message": f"Выбрано дней: {len(days)}. На эту точку разрешено "
                           f"{'не больше ' + str(max_here) if max_here > 1 else 'ровно один'}."}

    overloaded = [d for d in days if d in check.get("fullDays", [])]
    if overloaded:
        return {"success": False,
                "message": f"У {to_agent} в {', '.join(overloaded)} уже "
                           f"{MAX_VISITS_PER_DAY} визитов. Выберите другие дни."}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                info = await conn.fetchrow(
                    """
                    SELECT max(point_name) AS point_name,
                           string_agg(DISTINCT visit_day, ', ') AS days
                      FROM attachments
                     WHERE point_code = $1 AND upper(agent) = $2
                    """,
                    point_code, from_agent,
                )
                if info is None or info["point_name"] is None:
                    return {"success": False,
                            "message": f"{from_agent} не закреплён за этой точкой"}

                point_name = info["point_name"]
                old_days = info["days"] or ""

                await conn.execute(
                    "DELETE FROM attachments WHERE point_code = $1 AND upper(agent) = $2",
                    point_code, from_agent,
                )
                await conn.executemany(
                    """
                    INSERT INTO attachments
                        (point_code, point_name, agent_brand, agent, visit_day)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    [(point_code, point_name, agent_brand(to_agent), to_agent, d)
                     for d in days],
                )
                await conn.execute(
                    """
                    INSERT INTO transfer_log
                        (point_code, point_name, from_agent, to_agent, days, by_login)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    point_code, point_name, from_agent, to_agent,
                    ", ".join(days), (by_login or "").upper(),
                )
    except Exception as e:
        return _db_error(e)

    # Оба агента должны узнать: один потерял точку из маршрута,
    # другой получил её вместе с днями
    await _notify_transfer(from_agent, to_agent, point_name, point_code,
                           old_days, ", ".join(days))

    return {"success": True,
            "message": f"Точка передана: {from_agent} → {to_agent} ({', '.join(days)})"}


async def _notify_transfer(from_agent: str, to_agent: str, point_name: str,
                           point_code: str, old_days: str, new_days: str) -> None:
    """Сообщает обоим агентам о передаче точки."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT upper(login) AS login, tg_id FROM users "
                " WHERE upper(login) = ANY($1::text[]) AND tg_id IS NOT NULL",
                [from_agent, to_agent],
            )
    except Exception:
        logger.exception("Не удалось найти Telegram агентов для уведомления о передаче")
        return

    tg = {r["login"]: r["tg_id"] for r in rows}

    if tg.get(from_agent):
        await notify.notify_user(tg[from_agent], (
            "📤 ТОЧКА ПЕРЕДАНА ДРУГОМУ АГЕНТУ\n\n"
            f"🏪 {point_name}\n"
            f"🔢 Код: {point_code}\n"
            + (f"📅 Было: {old_days}\n" if old_days else "")
            + f"👤 Теперь её ведёт: {to_agent}\n\n"
            "Точка убрана из вашего маршрута."
        ))

    if tg.get(to_agent):
        await notify.notify_user(tg[to_agent], (
            "📥 ВАМ ПЕРЕДАНА ТОЧКА\n\n"
            f"🏪 {point_name}\n"
            f"🔢 Код: {point_code}\n"
            f"📅 Дни визита: {new_days}\n"
            f"👤 Передана от: {from_agent}\n\n"
            "Точка добавлена в ваш маршрут — она уже видна во вкладке «Мои точки»."
        ))


# Сколько живёт пропуск в панель. Сутки: смена достаточно длинная,
# а вечных пропусков быть не должно.
PANEL_SESSION_HOURS = 24


async def _ensure_sessions_table(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS panel_sessions (
            token      TEXT PRIMARY KEY,
            login      TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL
        )
    """)


async def panel_login(login_value: str, password: str) -> dict:
    """
    Вход в панель: проверяет пароль и выдаёт пропуск (токен).

    Дальше панель работает по токену, а логин с паролем больше никуда не
    передаются. Это и позволяет открывать панель из приложения агента без
    повторного ввода: пароль не пришлось бы тащить через адресную строку.
    """
    auth = await _check_panel(login_value, password)
    if not auth.get("success"):
        return auth

    token = secrets.token_urlsafe(24)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _ensure_sessions_table(conn)
            await conn.execute(
                """
                INSERT INTO panel_sessions (token, login, expires_at)
                VALUES ($1, $2, now() + ($3 || ' hours')::interval)
                """,
                token, login_value.strip().lower(), str(PANEL_SESSION_HOURS),
            )
            # Заодно подчищаем просроченные, чтобы таблица не росла
            await conn.execute("DELETE FROM panel_sessions WHERE expires_at < now()")
    except Exception as e:
        return _db_error(e)

    return {**auth, "token": token}


async def _check_token(token: str) -> dict:
    """Кто пришёл с этим пропуском."""
    token = (token or "").strip()
    if not token:
        return {"success": False, "message": "Нужно войти заново"}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _ensure_sessions_table(conn)
            login_value = await conn.fetchval(
                "SELECT login FROM panel_sessions WHERE token = $1 AND expires_at > now()",
                token,
            )
    except Exception as e:
        return _db_error(e)

    if not login_value:
        return {"success": False, "message": "Сессия истекла — войдите заново", "expired": True}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COALESCE(role, 'agent') AS role, brand
                  FROM users WHERE login = $1
                """,
                login_value,
            )
    except Exception as e:
        return _db_error(e)

    if row is None or row["role"] not in ("supervisor", "admin", "operator"):
        return {"success": False, "message": "Недостаточно прав"}

    return {
        "success": True,
        "role": row["role"],
        "isAdmin": row["role"] == "admin",
        "isOperator": row["role"] == "operator",
        "supervisor": login_value.upper() if row["role"] == "supervisor" else "",
        "brand": row["brand"] or "",
        "login": login_value,
    }


async def panel_logout(token: str) -> dict:
    """Гасит пропуск при выходе."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _ensure_sessions_table(conn)
            await conn.execute("DELETE FROM panel_sessions WHERE token = $1", (token or "").strip())
    except Exception as e:
        return _db_error(e)
    return {"success": True}


async def _check_panel(login_value: str, password: str) -> dict:
    """
    Проверяет доступ в панель и возвращает роль ИЗ БАЗЫ.

    Супервайзер видит только своих агентов, общий админ — всех.
    Роль и подчинённые берутся из базы, а не из запроса: иначе супервайзер,
    подставив чужой код, увидел бы чужих людей.
    """
    auth = await login(login_value, password)
    if not auth.get("success"):
        return {"success": False, "message": "Неверный логин или пароль"}

    role = auth.get("role")
    if role not in ("supervisor", "admin", "operator"):
        return {"success": False, "message": "Недостаточно прав"}

    return {
        "success": True,
        "role": role,
        "isAdmin": role == "admin",
        "isOperator": role == "operator",
        "supervisor": login_value.strip().upper() if role == "supervisor" else "",
        "brand": auth.get("brand") or "",
    }


async def _agent_allowed(auth: dict, agent: str) -> bool:
    """Свой ли это агент для вошедшего супервайзера."""
    if auth.get("isAdmin"):
        return True
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM users WHERE upper(login) = $1 AND upper(supervisor) = $2",
                (agent or "").strip().upper(), auth.get("supervisor", ""),
            )
        return row is not None
    except Exception:
        return False


async def panel_start(token: str) -> dict:
    """
    Первый экран зависит от роли:
      оператор    — лист заявок, больше ему ничего не нужно;
      админ       — список супервайзеров (и доступ к заявкам через вкладки);
      супервайзер — его агенты.
    """
    auth = await _check_token(token)
    if not auth.get("success"):
        return auth

    if auth["isOperator"]:
        result = await pending_requests()
        if result.get("success"):
            result.update({"isAdmin": False, "isOperator": True,
                           "login": auth["login"]})
        return result

    if auth["isAdmin"]:
        result = await list_supervisors()
        if result.get("success"):
            result.update({"isAdmin": True, "isOperator": False,
                           "supervisor": "", "login": auth["login"]})
        return result

    result = await list_agents(auth["supervisor"])
    if result.get("success"):
        result.update({"isAdmin": False, "isOperator": False,
                       "brand": auth["brand"], "login": auth["login"]})
    return result


async def panel_supervisors(token: str, search: str = "") -> dict:
    auth = await _check_token(token)
    if not auth.get("success"):
        return auth
    if not auth["isAdmin"]:
        return {"success": False, "message": "Недостаточно прав"}
    return await list_supervisors(search)


async def panel_agents(token: str, supervisor: str = "", search: str = "") -> dict:
    auth = await _check_token(token)
    if not auth.get("success"):
        return auth
    target = supervisor if auth["isAdmin"] else auth["supervisor"]
    return await list_agents(target, search)


async def panel_agent_points(token: str, agent: str, search: str = "") -> dict:
    auth = await _check_token(token)
    if not auth.get("success"):
        return auth
    if not await _agent_allowed(auth, agent):
        return {"success": False, "message": "Этот агент не в вашем подчинении"}
    return await agent_points(agent, "", search)


async def panel_point_details(token: str, point_code: str, agent: str = "") -> dict:
    auth = await _check_token(token)
    if not auth.get("success"):
        return auth
    if agent and not await _agent_allowed(auth, agent):
        return {"success": False, "message": "Этот агент не в вашем подчинении"}
    return await point_details(point_code, agent)


async def panel_unattach_days(token: str, agent: str, point_code: str, days: list) -> dict:
    auth = await _check_token(token)
    if not auth.get("success"):
        return auth
    if not await _agent_allowed(auth, agent):
        return {"success": False, "message": "Этот агент не в вашем подчинении"}
    return await unattach_days(agent, point_code, days)


async def panel_unattach_agent(token: str, agent: str, point_code: str) -> dict:
    auth = await _check_token(token)
    if not auth.get("success"):
        return auth
    # Чужого агента супервайзер может открепить только на точке, где стоит
    # кто-то из его подчинённых
    if not auth["isAdmin"] and not await _agent_allowed(auth, agent):
        detail = await point_details(point_code)
        mine = False
        for a in detail.get("agents", []):
            if await _agent_allowed(auth, a["agent"]):
                mine = True
                break
        if not mine:
            return {"success": False, "message": "На этой точке нет ваших агентов"}
    return await unattach_agent(agent, point_code)


async def panel_set_point_type(token: str, point_code: str, point_type: str) -> dict:
    auth = await _check_token(token)
    if not auth.get("success"):
        return auth
    return await set_point_type(point_code, point_type)


async def panel_set_point_top(token: str, point_code: str, is_top: bool) -> dict:
    auth = await _check_token(token)
    if not auth.get("success"):
        return auth
    if auth["isOperator"]:
        return {"success": False, "message": "Недостаточно прав"}
    return await set_point_top(point_code, is_top)


async def panel_transfer_candidates(token: str, agent: str, point_code: str) -> dict:
    """Кому супервайзер может отдать точку."""
    auth = await _check_token(token)
    if not auth.get("success"):
        return auth
    if auth["isOperator"]:
        return {"success": False, "message": "Недостаточно прав"}
    if not await _agent_allowed(auth, agent):
        return {"success": False, "message": "Этот агент не в вашем подчинении"}

    return await transfer_candidates(
        agent, point_code,
        supervisor=auth.get("supervisor", ""),
        any_agent=auth["isAdmin"],
    )


async def panel_transfer_point(token: str, point_code: str, from_agent: str,
                               to_agent: str, days: list) -> dict:
    """
    Передача точки. Проверяются ОБА агента: и тот, у кого забираем,
    и тот, кому отдаём. Иначе супервайзер, подставив чужой логин, мог бы
    сгрузить точку в другую команду.
    """
    auth = await _check_token(token)
    if not auth.get("success"):
        return auth
    if auth["isOperator"]:
        return {"success": False, "message": "Недостаточно прав"}

    if not await _agent_allowed(auth, from_agent):
        return {"success": False, "message": "Этот агент не в вашем подчинении"}
    if not await _agent_allowed(auth, to_agent):
        return {"success": False,
                "message": f"{to_agent.upper()} не в вашем подчинении — "
                           f"передать точку можно только своему агенту"}

    return await transfer_point(point_code, from_agent, to_agent, days,
                                by_login=auth["login"])


async def panel_change_password(token: str, current_password: str, new_password: str) -> dict:
    """Смена пароля из панели — по пропуску, логин берётся из сессии."""
    auth = await _check_token(token)
    if not auth.get("success"):
        return auth
    return await change_password(auth["login"], current_password, new_password)


# ======================================================================
# ПОИСК ТОЧКИ ПО ИНН
# ======================================================================

# Приставки и суффиксы организационных форм: при сравнении названий они
# только мешают — "OSIYO MARKET" и "OSIYO MARKET MCHJ" это одна точка.
LEGAL_FORMS = re.compile(r"\b(OOO|MCHJ|YATT|YTT|XK|QK|MSHJ|ООО|МЧЖ|ЯТТ)\b", re.I)


def normalize_name(name: str) -> str:
    """Приводит название к виду, удобному для сравнения."""
    val = (name or "").upper()
    val = LEGAL_FORMS.sub(" ", val)
    val = re.sub(r"[^A-Z0-9\s]", " ", val)
    return re.sub(r"\s+", " ", val).strip()


async def check_inn(inn: str) -> dict:
    """
    Ищет точки по ИНН.

    Возвращает СПИСОК: у одной точки бывает несколько кодов контрагента
    с одним ИНН (KALINA, UNILEVER, NIVEA — разные категории поставки),
    и агент должен выбрать нужный код сам.
    """
    inn = re.sub(r"[^0-9]", "", inn or "")
    if not inn:
        return {"success": False, "message": "Введите ИНН цифрами"}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT point_code, point_name, status
                  FROM client_base
                 WHERE inn = $1
                 ORDER BY point_code
                """,
                inn,
            )
    except Exception as e:
        return _db_error(e)

    if not rows:
        return {"success": True, "exists": False}

    points = [
        {
            "pointCode": r["point_code"],
            "pointName": r["point_name"],
            "status": r["status"] or 0,
        }
        for r in rows
    ]

    first = points[0]
    return {
        "success": True,
        "exists": True,
        "points": points,
        # Поля ниже — для случая с единственным кодом
        "pointCode": first["pointCode"],
        "pointName": first["pointName"],
        "status": first["status"],
    }


# ----------------------------------------------------------------------
# ЛИМИТЫ НАГРУЗКИ АГЕНТА
#
# Правила установлены менеджментом и проверяются ЗДЕСЬ, на сервере, а не
# в интерфейсе: бот и сайт — два независимых клиента, и любой из них может
# отправить запрос в обход формы.
#
# Старые перекосы (у кого уже 200 точек) правилами не ломаются: лимит
# останавливает только НОВОЕ прикрепление. Разбирать накопленное —
# работа супервайзера в панели.
# ----------------------------------------------------------------------
MAX_POINTS_PER_AGENT = 150      # не больше 150 торговых точек на агента
MAX_VISITS_PER_DAY = 36         # не больше 36 визитов в один день недели

# Сколько дней визита агент может занять на ОДНОЙ точке.
#
# Обычный ритейл объезжают раз в неделю: агент проходит территорию по
# маршруту и заходит на точку один раз. Когда точка стояла на нескольких
# днях, заказы начинали пробивать вне маршрута — ради этого правило
# и вводится.
#
# Три дня оставлены только ТОП-точкам: к ним ездят чаще по обороту.
MAX_VISIT_DAYS = 3              # ТОП-точка
MAX_VISIT_DAYS_REGULAR = 1      # обычная точка


def max_days_for(is_top: bool) -> int:
    """Сколько дней визита разрешено на этой точке."""
    return MAX_VISIT_DAYS if is_top else MAX_VISIT_DAYS_REGULAR

# Узбекистан круглый год живёт в UTC+5, перевода часов нет.
# Нужен для «сегодняшнего дня визита» и для архива по дням.
TIMEZONE = "Asia/Tashkent"


def agent_brand(agent: str) -> str:
    """
    Бренд агента — РОВНО первые два символа логина.

    Логины бывают разной длины (OR0114, ORTP0101, UL0112, ULTP0101), но
    бренд определяют именно две первые буквы: UL0112 и ULTP0101 — один и
    тот же бренд UL, и на одной точке они стоять не могут.
    """
    return (agent or "").strip()[:2].upper()


def split_days(visit_day: str) -> list[str]:
    """'Понедельник, Среда' → ['Понедельник', 'Среда']"""
    return [d.strip() for d in (visit_day or "").split(",") if d.strip()]


# Чем считается «одна точка» для лимитов.
#
# У одной физической точки бывает несколько кодов контрагента с общим ИНН
# (KALINA, UNILEVER, NIVEA — разные категории поставки). Агент приезжает
# туда ОДИН раз, поэтому и место в лимите должно занимать одно. Считаем
# по ИНН; у служебных строк без ИНН место считается по коду, иначе все
# они слились бы в одну «точку» с пустым ИНН.
POINT_KEY = "COALESCE(NULLIF(c.inn, ''), a.point_code)"


async def agent_load(agent: str) -> dict:
    """
    Текущая нагрузка агента: сколько у него точек и сколько визитов
    в каждый день недели.

    И точки, и визиты считаются по уникальным ИНН (см. POINT_KEY):
    три кода одной точки — одно место в лимите и один визит в день.
    """
    agent = (agent or "").strip().upper()
    if not agent:
        return {"success": False, "message": "Не указан агент"}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            points = await conn.fetchval(
                f"""
                SELECT count(DISTINCT {POINT_KEY})
                  FROM attachments a
             LEFT JOIN client_base c ON c.point_code = a.point_code
                 WHERE upper(a.agent) = $1
                """,
                agent,
            )
            rows = await conn.fetch(
                f"""
                SELECT a.visit_day, count(DISTINCT {POINT_KEY}) AS visits
                  FROM attachments a
             LEFT JOIN client_base c ON c.point_code = a.point_code
                 WHERE upper(a.agent) = $1
                   AND a.visit_day IS NOT NULL AND a.visit_day <> ''
                 GROUP BY a.visit_day
                """,
                agent,
            )
    except Exception as e:
        return _db_error(e)

    day_load = {r["visit_day"].strip(): r["visits"] for r in rows if r["visit_day"]}
    used = sum(day_load.values())

    return {
        "success": True,
        "agent": agent,
        "pointCount": points or 0,
        "dayLoad": day_load,
        "fullDays": sorted(d for d, n in day_load.items() if n >= MAX_VISITS_PER_DAY),
        "weekVisits": used,
        "weekCapacity": MAX_VISITS_PER_DAY * len(WORK_DAYS),
        "maxPoints": MAX_POINTS_PER_AGENT,
        "maxVisitsPerDay": MAX_VISITS_PER_DAY,
        "maxDays": MAX_VISIT_DAYS,
        "maxDaysRegular": MAX_VISIT_DAYS_REGULAR,
    }


async def check_attach_allowed(point_code: str, agent: str,
                               ignore_agent: str = "") -> dict:
    """
    Можно ли агенту прикрепиться к этой точке.

    ignore_agent — агент, которого на этой точке как бы нет. Нужен при
    передаче: принимающего проверяем так, будто отдающий уже снят, иначе
    он сам себя и заблокирует правилом «один агент бренда на точке».

    Правила:
      1. Точка занята другим агентом того же бренда — прикрепление запрещено
         (OR0104 блокирует OR0111, но не UL1111). На сетевой точке разрешено.
      2. Сколько дней можно занять на точке, зависит от неё самой:
         ТОП-точка — до MAX_VISIT_DAYS, обычная — ровно один день.
         Обычный ритейл объезжают раз в неделю, и лишние дни приводили
         к заказам вне маршрута.
      3. Не больше MAX_POINTS_PER_AGENT точек на агента, счёт по уникальным
         ИНН. Правило касается только НОВЫХ для агента точек: добавить день
         на точку, которая у него уже есть, можно и на лимите — число точек
         от этого не растёт.
      4. Не больше MAX_VISITS_PER_DAY визитов в один день недели.
         Переполненные дни не запрещают прикрепление целиком — они просто
         исчезают из выбора (fullDays). Запрет только если свободных
         дней не осталось совсем.

    Возвращает:
      allowed         — можно ли продолжать
      reason          — 'brand' | 'limit' | 'points' | 'day_limit' | None
      blockedBy       — логин агента, занявшего точку (для reason='brand')
      myDays          — дни, которые агент уже занял на этой точке
      remaining       — сколько дней ещё можно выбрать
      fullDays        — дни, где у агента уже лимит визитов (выбирать нельзя)
      freeDays        — дни, которые реально доступны к выбору
      isTop           — ТОП ли эта точка
      maxDaysHere     — сколько дней разрешено именно на этой точке
      pointCount      — сколько точек у агента сейчас
      maxPoints       — лимит точек
      maxVisitsPerDay — лимит визитов в день
    """
    brand = agent_brand(agent)

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT agent, visit_day
                  FROM attachments
                 WHERE point_code = $1
                """,
                point_code,
            )
            # На сетевой точке несколько агентов одного бренда — норма,
            # а ТОП определяет, сколько дней можно занять
            info = await conn.fetchrow(
                """
                SELECT COALESCE(type, 'def')    AS type,
                       COALESCE(is_top, false)  AS is_top,
                       NULLIF(inn, '')          AS inn
                  FROM client_base WHERE point_code = $1
                """,
                point_code,
            )

            # Дни, которые агент уже занял на ЭТОЙ ЖЕ физической точке —
            # то есть на любом коде контрагента с тем же ИНН. Иначе агент
            # взял бы код KALINA на понедельник, код NIVEA того же магазина
            # на вторник и приезжал бы туда дважды в неделю, обойдя правило.
            inn = info["inn"] if info else None
            sibling_days = []
            if inn:
                sibling_days = await conn.fetch(
                    """
                    SELECT DISTINCT a.visit_day
                      FROM attachments a
                      JOIN client_base c ON c.point_code = a.point_code
                     WHERE upper(a.agent) = $1
                       AND NULLIF(c.inn, '') = $2
                       AND a.point_code <> $3
                       AND a.visit_day IS NOT NULL AND a.visit_day <> ''
                    """,
                    (agent or "").upper(), inn, point_code,
                )
    except Exception as e:
        return _db_error(e)

    load = await agent_load(agent)
    if not load.get("success"):
        return load

    is_top = bool(info["is_top"]) if info else False
    max_days_here = max_days_for(is_top)

    limits = {
        "pointCount": load["pointCount"],
        "maxPoints": MAX_POINTS_PER_AGENT,
        "maxVisitsPerDay": MAX_VISITS_PER_DAY,
        "maxDays": max_days_here,
        "maxDaysHere": max_days_here,
        "isTop": is_top,
        "dayLoad": load["dayLoad"],
        "fullDays": load["fullDays"],
    }

    is_chain = (info["type"] if info else "def") == "chain"

    ignore = (ignore_agent or "").strip().upper()

    my_days, other_agent = [], None
    for r in rows:
        row_agent = (r["agent"] or "").upper()
        if row_agent == ignore:
            # Этого агента с точки снимают прямо сейчас — он не помеха
            continue
        if row_agent == (agent or "").upper():
            my_days.extend(split_days(r["visit_day"]))
        elif agent_brand(row_agent) == brand and brand and not is_chain:
            # Точку уже занял коллега по бренду (на сетевой это разрешено)
            other_agent = other_agent or row_agent

    # Дни на других кодах того же ИНН — это те же поездки на ту же точку
    my_days.extend(d["visit_day"].strip() for d in sibling_days if d["visit_day"])

    # Один и тот же день мог попасть в две записи — считаем уникальные
    my_days = list(dict.fromkeys(my_days))

    def answer(allowed, reason, **extra):
        return {
            "success": True, "allowed": allowed, "reason": reason,
            "blockedBy": None, "myDays": my_days, "remaining": 0,
            "freeDays": [], **limits, **extra,
        }

    if other_agent:
        return answer(False, "brand", blockedBy=other_agent)

    # Правило 3. Точка новая для агента? Тогда она увеличит их число.
    # «Новая» — значит у агента нет ни одного дня на этом ИНН: другой код
    # того же магазина места в лимите не добавит, оно уже занято.
    is_new_point = not my_days
    if is_new_point and load["pointCount"] >= MAX_POINTS_PER_AGENT:
        return answer(False, "points")

    remaining = max_days_here - len(my_days)
    if remaining <= 0:
        # На обычной точке это значит «день уже есть», на ТОП — «занято три»
        return answer(False, "limit")

    # Правило 4. Дни, где агент уже выбрал норму визитов, недоступны
    free_days = [d for d in WORK_DAYS
                 if d not in my_days and d not in load["fullDays"]]
    if not free_days:
        return answer(False, "day_limit")

    return answer(True, None, remaining=min(remaining, len(free_days)),
                  freeDays=free_days)


async def search_similar_points(name: str, limit: int = SIMILAR_LIMIT) -> dict:
    """
    Ищет в базе точки с похожим названием.

    Нужно для случая, когда агент ошибся в ИНН и пошёл добавлять точку,
    которая на самом деле уже есть: "MUHAYO TRADE" против "MUXAYYO TRADE"
    в базе. Сравнение идёт по нормализованным названиям (без MCHJ, YATT
    и знаков препинания) через pg_trgm.
    """
    normalized = normalize_name(name)
    if len(normalized) < 3:
        # По двум буквам похожим окажется пол-базы
        return {"success": True, "matches": []}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT point_code, point_name, inn, status,
                       similarity(upper(point_name), $1) AS sim
                  FROM client_base
                 WHERE upper(point_name) % $1
                 ORDER BY sim DESC
                 LIMIT $2
                """,
                normalized, limit,
            )
    except Exception as e:
        return _db_error(e)

    matches = [
        {
            "pointCode": r["point_code"],
            "pointName": r["point_name"],
            "inn": r["inn"],
            "status": r["status"] or 0,
            "similarity": round(float(r["sim"]), 3),
        }
        for r in rows if float(r["sim"]) >= SIMILARITY_THRESHOLD
    ]
    return {"success": True, "matches": matches}


# ======================================================================
# ПРИКРЕПЛЕНИЕ ТОЧКИ
# ======================================================================

async def attach(agent: str, point_code: str, point_name: str, visit_day: str,
                 tg_id: int | None = None, source: str = "") -> dict:
    """
    Прикрепляет точку к агенту.

    Правила проверяются ЗДЕСЬ, а не только в интерфейсе: бот и сайт — это
    два независимых клиента, и любой из них может отправить запрос с лишними
    днями (так и случилось: сайт ограничивал выбор занятых дней, но не их
    количество, и у агента набралось пять дней вместо трёх).
    """
    denied = await _require_agent(agent)
    if denied:
        return denied

    brand = agent_brand(agent)
    days = split_days(visit_day)

    if not days:
        return {"success": False, "message": "Не выбран ни один день визита"}

    # Один и тот же день дважды в одном запросе — не ошибка агента, а недосмотр
    # формы, но в базу он попасть не должен: два визита в понедельник займут
    # два места из трёх
    repeated = [d for d in set(days) if days.count(d) > 1]
    if repeated:
        return {"success": False,
                "message": f"Один и тот же день выбран несколько раз: {', '.join(repeated)}"}

    check = await check_attach_allowed(point_code, agent)
    if not check.get("success"):
        return check

    if not check.get("allowed"):
        return {"success": False, "message": attach_denied_text(check)}

    my_days = check.get("myDays", [])
    remaining = check.get("remaining", MAX_VISIT_DAYS)
    full_days = check.get("fullDays", [])

    duplicates = [d for d in days if d in my_days]
    if duplicates:
        return {"success": False,
                "message": f"Эти дни у вас уже заняты на этой точке: {', '.join(duplicates)}"}

    # Лимит визитов в день — вторая проверка после check_attach_allowed:
    # там дни только помечаются переполненными, а здесь уже приходит
    # конкретный выбор, и его нужно сверить с этой пометкой
    overloaded = [d for d in days if d in full_days]
    if overloaded:
        return {"success": False,
                "message": f"В {', '.join(overloaded)} у вас уже {MAX_VISITS_PER_DAY} визитов — "
                           f"это предел на один день. Выберите другой день."}

    max_here = check.get("maxDaysHere", MAX_VISIT_DAYS)
    if len(days) > remaining:
        limit_text = (f"не больше {max_here} дней" if max_here > 1
                      else "ровно один день в неделю")
        return {"success": False,
                "message": f"Можно выбрать ещё {remaining}, а выбрано {len(days)}. "
                           f"На эту точку — {limit_text}."}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Каждый день визита — отдельная строка: так их удобно
            # фильтровать и считать, не разбирая текст через запятую
            await conn.executemany(
                """
                INSERT INTO attachments
                    (point_code, point_name, agent_brand, agent, visit_day)
                VALUES ($1, $2, $3, $4, $5)
                """,
                [(point_code, point_name, brand, agent, day) for day in days],
            )
    except Exception as e:
        return _db_error(e)

    # Заявка в лист оператора. Прикрепление в базе уже состоялось — оператору
    # остаётся провести его в учётной системе, и «Готово» отмечает именно это.
    await create_request({
        "kind": "attach",
        "agent": agent,
        "pointCode": point_code,
        "pointName": point_name,
        "visitDay": ", ".join(days),
        "tgId": tg_id,
        "source": source,
    })

    return {"success": True, "message": "Точка успешно прикреплена к вам!"}


async def _require_agent(login_value: str) -> dict | None:
    """
    Точки прикрепляет и добавляет только агент.

    Проверка на сервере, а не в интерфейсе: перенаправление в панель — это
    удобство, и одна забытая роль в списке на странице уже приводила к тому,
    что оператор оказывался на экране агента. Здесь роль берётся из базы,
    поэтому подставить чужой логин в запрос бесполезно.

    Возвращает None, если всё в порядке, иначе готовый отказ.
    """
    login_value = (login_value or "").strip().lower()
    if not login_value:
        return {"success": False, "message": "Не указан агент"}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            role = await conn.fetchval(
                "SELECT COALESCE(role, 'agent') FROM users WHERE lower(login) = $1",
                login_value,
            )
    except Exception as e:
        return _db_error(e)

    if role is None:
        return {"success": False, "message": "Такого агента нет в базе"}

    if role != "agent":
        return {"success": False,
                "message": "Этот логин не агентский — точки прикрепляют только агенты"}

    return None


def attach_denied_text(check: dict) -> str:
    """
    Один текст отказа для всех клиентов: бот, сайт и повторная проверка
    внутри attach() должны объяснять запрет одинаково.
    """
    reason = check.get("reason")

    if reason == "brand":
        return (f"В этой точке закреплён другой агент вашего бренда "
                f"({check.get('blockedBy')})")

    if reason == "points":
        return (f"У вас уже {check.get('pointCount', MAX_POINTS_PER_AGENT)} торговых точек — "
                f"это предел ({MAX_POINTS_PER_AGENT}). Чтобы взять новую, "
                f"освободите лишние через супервайзера.")

    if reason == "day_limit":
        return (f"Во всех рабочих днях у вас уже по {MAX_VISITS_PER_DAY} визитов — "
                f"это предел на один день. Свободных дней не осталось.")

    # reason == 'limit' — дни на точке кончились. Причина разная:
    # обычную точку посещают раз в неделю, ТОП — до трёх раз
    days = ", ".join(check.get("myDays", []))
    if not check.get("isTop"):
        return (f"Эта точка уже закреплена за вами на {days}. "
                f"Обычная торговая точка закрепляется на один день в неделю — "
                f"территорию обходят раз в неделю. Три дня бывают только "
                f"у ТОП-точек, отметить такую может супервайзер.")

    return f"У вас уже {MAX_VISIT_DAYS} дня в этой ТОП-точке: {days}"


# ======================================================================
# ДОБАВЛЕНИЕ НОВОЙ ТОЧКИ
# ======================================================================

async def add_tt(data: dict) -> dict:
    """
    data приходит из bot.py в тех же ключах, что раньше уходили в Apps Script
    (clientName, deliveryCode, visitDay), поэтому здесь они раскладываются
    по колонкам таблицы add_requests.
    """
    denied = await _require_agent(data.get("agent"))
    if denied:
        return denied

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO add_requests (
                    client_name, geo, address, phone, inn,
                    region, oblast, okrug, rayon,
                    "format", channel, "type", category, delivery_code,
                    agent, visit_day, comments
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9,
                        $10, $11, $12, $13, $14, $15, $16, $17)
                """,
                data.get("clientName"),
                data.get("geo"),
                data.get("address"),
                data.get("phone"),
                data.get("inn"),
                data.get("region"),
                data.get("oblast"),
                data.get("okrug"),
                data.get("rayon"),
                data.get("format"),
                data.get("channel"),
                data.get("type"),
                data.get("category"),
                data.get("deliveryCode"),
                data.get("agent"),
                data.get("visitDay"),
                data.get("comments"),
            )
    except Exception as e:
        return _db_error(e)

    # Заявка в лист оператора: новую точку кто-то должен завести в учётной
    # системе, и до этого момента агент должен видеть её как «в обработке»
    await create_request({
        "kind": "add",
        "agent": data.get("agent"),
        "pointName": data.get("clientName"),
        "inn": data.get("inn"),
        "visitDay": data.get("visitDay"),
        "tgId": data.get("tgId"),
        "source": data.get("source"),
        "payload": data,
    })

    return {"success": True, "message": "Новая торговая точка успешно добавлена!"}


# ======================================================================
# ЗАЯВКИ ОПЕРАТОРА
#
# Очередь обработки: агент отправил заявку — оператор провёл её в учётной
# системе и отметил «Готово». Сами данные лежат в attachments и
# add_requests, здесь только состояние обработки.
# ======================================================================

# Поля, которые в карточке заявки показывать не нужно: агент и дни визита
# выведены отдельными строками, а служебные ключи запроса оператору
# ни о чём не говорят
PAYLOAD_SKIP = {"action", "token", "agent", "tgId", "source", "kind", "visitDay"}


async def create_request(data: dict) -> dict:
    """
    Кладёт заявку в очередь оператора.

    Ошибка здесь НЕ ломает основной сценарий: прикрепление или добавление
    точки уже записаны в базу, и агент не должен получить «не получилось»
    из-за очереди. Поэтому ответ не проверяется вызывающей стороной,
    а сбой уходит в лог.
    """
    agent = (data.get("agent") or "").strip().upper()
    if not agent:
        return {"success": False, "message": "Не указан агент"}

    payload = data.get("payload") or {}
    clean = {k: v for k, v in payload.items()
             if k not in PAYLOAD_SKIP and v not in (None, "")}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            request_id = await conn.fetchval(
                """
                INSERT INTO operator_requests
                    (kind, agent, agent_brand, tg_id, point_code, point_name,
                     inn, visit_day, source, payload)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
             RETURNING id
                """,
                data.get("kind") or "attach",
                agent,
                agent_brand(agent),
                data.get("tgId"),
                data.get("pointCode"),
                data.get("pointName"),
                data.get("inn"),
                data.get("visitDay"),
                data.get("source") or "",
                json.dumps(clean, ensure_ascii=False),
            )
    except Exception as e:
        logger.exception("Заявку не удалось положить в очередь оператора")
        return {"success": False, "message": str(e)}

    return {"success": True, "id": request_id}


async def remember_tg_id(login_value: str, tg_id: int) -> None:
    """
    Запоминает Telegram-адрес агента при входе в бота.

    Без него бот не может написать агенту первым: уведомление о готовности
    заявки приходит, когда никакого входящего сообщения нет.
    """
    if not tg_id:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET tg_id = $2 WHERE login = $1",
                (login_value or "").strip().lower(), int(tg_id),
            )
    except Exception:
        # Вход важнее: если не записалось — агент просто не получит
        # уведомление, но работать сможет
        logger.exception("Не удалось запомнить tg_id агента %s", login_value)


def _request_row(r) -> dict:
    return {
        "id": r["id"],
        "kind": r["kind"],
        "kindLabel": "Добавление ТТ" if r["kind"] == "add" else "Прикрепление",
        "agent": r["agent"],
        "pointCode": r["point_code"] or "",
        "pointName": r["point_name"] or "—",
        "inn": r["inn"] or "",
        "visitDay": r["visit_day"] or "",
        "source": r["source"] or "",
        "status": r["status"],
        "createdAt": r["created_at"].isoformat() if r["created_at"] else "",
        "doneAt": r["done_at"].isoformat() if r["done_at"] else "",
        "doneBy": r["done_by"] or "",
        "details": dict(json.loads(r["payload"])) if r["payload"] else {},
    }


REQUEST_COLUMNS = """
    id, kind, agent, point_code, point_name, inn, visit_day,
    source, status, created_at, done_at, done_by, payload
"""


async def pending_requests(search: str = "", limit: int = 200) -> dict:
    """Лист оператора: заявки, которые ждут обработки. Новые сверху."""
    like = f"%{(search or '').strip().upper()}%" if (search or "").strip() else ""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {REQUEST_COLUMNS}
                  FROM operator_requests
                 WHERE status = 'pending'
                   AND ($1 = ''
                        OR upper(agent) LIKE $1
                        OR upper(COALESCE(point_name, '')) LIKE $1
                        OR COALESCE(point_code, '') LIKE $1
                        OR COALESCE(inn, '') LIKE $1)
                 ORDER BY created_at DESC
                 LIMIT $2
                """,
                like, limit,
            )
    except Exception as e:
        return _db_error(e)

    return {"success": True, "requests": [_request_row(r) for r in rows]}


async def mark_request_done(request_id: int, operator_login: str) -> dict:
    """
    Отмечает заявку выполненной и уведомляет агента через бота.

    Условие status = 'pending' в UPDATE защищает от двойного нажатия:
    если заявку уже закрыл другой оператор, RETURNING вернёт пусто,
    и второе уведомление агенту не уйдёт.
    """
    try:
        request_id = int(request_id)
    except (TypeError, ValueError):
        return {"success": False, "message": "Не указана заявка"}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE operator_requests
                   SET status = 'done', done_at = now(), done_by = $2
                 WHERE id = $1 AND status = 'pending'
             RETURNING id, kind, agent, point_name, point_code, visit_day, tg_id
                """,
                request_id, (operator_login or "").strip().upper(),
            )
    except Exception as e:
        return _db_error(e)

    if row is None:
        return {"success": False, "message": "Заявка уже обработана — обновите лист"}

    # Уведомление агенту. Сбой отправки заявку не отменяет: она уже закрыта,
    # и оператор не должен нажимать «Готово» второй раз из-за Telegram
    await _notify_agent_done(row)

    return {"success": True, "message": "Заявка перенесена в архив", "id": row["id"]}


async def _notify_agent_done(row) -> None:
    """Текстовое сообщение агенту о том, что его заявку провели."""
    tg_id = row["tg_id"]
    if not tg_id:
        # Заявка пришла с сайта и агент ни разу не входил в бота —
        # берём адрес из его учётной записи
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                tg_id = await conn.fetchval(
                    "SELECT tg_id FROM users WHERE upper(login) = $1",
                    (row["agent"] or "").upper(),
                )
        except Exception:
            logger.exception("Не удалось найти Telegram агента %s", row["agent"])
            return

    if not tg_id:
        logger.info("У агента %s нет Telegram — уведомление не отправлено", row["agent"])
        return

    what = "Добавление новой ТТ" if row["kind"] == "add" else "Прикрепление точки"
    text = (
        "✅ ВАША ЗАЯВКА ВЫПОЛНЕНА\n\n"
        f"📋 {what}\n"
        f"🏪 {row['point_name'] or '—'}\n"
        + (f"🔢 Код: {row['point_code']}\n" if row["point_code"] else "")
        + (f"📅 Дни визита: {row['visit_day']}\n" if row["visit_day"] else "")
        + "\nОператор провёл заявку в системе. В приложении она стала зелёной."
    )

    await notify.notify_user(tg_id, text)


async def archive_requests(day: str = "", search: str = "", limit: int = 300) -> dict:
    """
    Архив: обработанные заявки за один день.

    day — 'ГГГГ-ММ-ДД' по ташкентскому времени. Без него берётся сегодня:
    архив открывают в первую очередь, чтобы посмотреть текущую смену.

    Сравнение идёт через AT TIME ZONE: done_at хранится в UTC, и без
    перевода заявки, закрытые вечером, попадали бы в следующий день.
    """
    day = (day or "").strip()
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            if not day:
                day = str(await conn.fetchval(
                    "SELECT (now() AT TIME ZONE $1)::date", TIMEZONE))

            # Двойное приведение $2::text::date обязательно. При простом
            # $2::date драйвер решает, что аргумент — объект даты Python,
            # и отказывается принимать строку '2026-09-18' из запроса.

            like = f"%{(search or '').strip().upper()}%" if (search or "").strip() else ""

            rows = await conn.fetch(
                f"""
                SELECT {REQUEST_COLUMNS}
                  FROM operator_requests
                 WHERE status = 'done'
                   AND (done_at AT TIME ZONE $1)::date = $2::text::date
                   AND ($3 = ''
                        OR upper(agent) LIKE $3
                        OR upper(COALESCE(point_name, '')) LIKE $3
                        OR COALESCE(point_code, '') LIKE $3
                        OR COALESCE(inn, '') LIKE $3)
                 ORDER BY done_at DESC
                 LIMIT $4
                """,
                TIMEZONE, day, like, limit,
            )

            # Дни, в которые вообще что-то делали: календарь помечает их
            # точкой, чтобы не тыкать в пустые даты наугад
            busy = await conn.fetch(
                f"""
                SELECT (done_at AT TIME ZONE $1)::date AS d, count(*) AS n
                  FROM operator_requests
                 WHERE status = 'done'
                   AND done_at > now() - interval '120 days'
                 GROUP BY 1
                 ORDER BY 1 DESC
                """,
                TIMEZONE,
            )
    except Exception as e:
        return _db_error(e)

    return {
        "success": True,
        "day": day,
        "requests": [_request_row(r) for r in rows],
        "busyDays": {str(b["d"]): b["n"] for b in busy},
    }


async def my_requests(agent: str, limit: int = 60) -> dict:
    """
    «Мои заявки» в приложении агента: что отправлено и что уже проведено.

    Выполненные не скрываются — агент должен увидеть, что заявка позеленела.
    """
    agent = (agent or "").strip().upper()
    if not agent:
        return {"success": False, "message": "Не указан агент"}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {REQUEST_COLUMNS}
                  FROM operator_requests
                 WHERE upper(agent) = $1
                 ORDER BY (status = 'pending') DESC, created_at DESC
                 LIMIT $2
                """,
                agent, limit,
            )
    except Exception as e:
        return _db_error(e)

    requests = [_request_row(r) for r in rows]
    return {
        "success": True,
        "requests": requests,
        "pendingCount": sum(1 for r in requests if r["status"] == "pending"),
    }


# Порядковый номер дня недели у Postgres (isodow): 1 = понедельник.
# Совпадает с порядком в WORK_DAYS, поэтому индекс берётся вычитанием.
async def today_name() -> str:
    """Название сегодняшнего дня по-русски, по ташкентскому времени."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            dow = await conn.fetchval(
                "SELECT extract(isodow FROM (now() AT TIME ZONE $1))::int", TIMEZONE)
    except Exception:
        logger.exception("Не удалось определить текущий день")
        return ""

    idx = int(dow) - 1
    return WORK_DAYS[idx] if 0 <= idx < len(WORK_DAYS) else ""


async def day_points(agent: str, day: str = "") -> dict:
    """
    Точки агента на один день визита — третья вкладка приложения.

    day пустой  → сегодняшний день (в выходной список будет пуст, и это
                  честно: визитов в этот день нет).
    day = 'all' → все точки агента.
    """
    agent = (agent or "").strip().upper()
    if not agent:
        return {"success": False, "message": "Не указан агент"}

    today = await today_name()
    day = (day or "").strip()
    if not day:
        day = today or "all"

    all_days = day == "all"

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT t.point_code,
                       COALESCE(c.point_name, max(t.point_name))   AS point_name,
                       c.inn,
                       COALESCE(c.status, 0)                       AS status,
                       string_agg(DISTINCT t.visit_day, ', ')      AS days
                  FROM attachments t
             LEFT JOIN client_base c ON c.point_code = t.point_code
                 WHERE upper(t.agent) = $1
                   AND ($2 OR t.visit_day = $3)
                 GROUP BY t.point_code, c.point_name, c.inn, c.status
                 ORDER BY point_name
                """,
                agent, all_days, day,
            )
    except Exception as e:
        return _db_error(e)

    load = await agent_load(agent)

    return {
        "success": True,
        "agent": agent,
        "day": day,
        "today": today,
        "workDays": list(WORK_DAYS),
        "points": [
            {
                "pointCode": r["point_code"],
                "pointName": r["point_name"] or "—",
                "inn": r["inn"] or "",
                "status": r["status"],
                "days": r["days"] or "",
            }
            for r in rows
        ],
        "pointCount": load.get("pointCount", 0),
        "dayLoad": load.get("dayLoad", {}),
        "maxPoints": MAX_POINTS_PER_AGENT,
        "maxVisitsPerDay": MAX_VISITS_PER_DAY,
    }


# ---------- Обёртки для панели: всё по пропуску ----------

async def panel_requests(token: str, search: str = "") -> dict:
    auth = await _check_token(token)
    if not auth.get("success"):
        return auth
    if not (auth["isOperator"] or auth["isAdmin"]):
        return {"success": False, "message": "Недостаточно прав"}
    result = await pending_requests(search)
    if result.get("success"):
        result["login"] = auth["login"]
    return result


async def panel_request_done(token: str, request_id: int) -> dict:
    auth = await _check_token(token)
    if not auth.get("success"):
        return auth
    if not (auth["isOperator"] or auth["isAdmin"]):
        return {"success": False, "message": "Недостаточно прав"}
    return await mark_request_done(request_id, auth["login"])


async def panel_archive(token: str, day: str = "", search: str = "") -> dict:
    auth = await _check_token(token)
    if not auth.get("success"):
        return auth
    if not (auth["isOperator"] or auth["isAdmin"]):
        return {"success": False, "message": "Недостаточно прав"}
    return await archive_requests(day, search)
