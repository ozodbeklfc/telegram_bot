"""
Работа с Postgres напрямую (без Google Apps Script).

Интерфейс функций (login / check_inn / attach / add_tt) специально оставлен
прежним: те же аргументы, те же ключи в ответе. Поэтому bot.py менять
из-за перехода на базу не пришлось.

Все функции возвращают словарь и НИКОГДА не бросают исключение наружу —
при проблеме с базой вернётся {"success": False, "message": "..."},
которое bot.py уже умеет показывать пользователю.
"""

import logging
import re

import asyncpg

from config import DATABASE_URL

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
                       -- берём первые две буквы логина. У админа пустой
                       -- бренд означает «общий доступ ко всем брендам»,
                       -- поэтому подставлять туда буквы логина нельзя.
                       CASE WHEN COALESCE(role, 'agent') = 'admin'
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
                -- на точке несколько агентов одного бренда (сетевые не в счёт)
                same_brand AS (
                    SELECT a.point_code
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
                           WHERE t.point_code IN (SELECT point_code FROM same_brand)
                              OR (m.agent, t.point_code) IN (SELECT agent, point_code FROM too_many)
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
        if r["day_count"] > MAX_VISIT_DAYS:
            problems.append("days")
        if r["agents_same_brand"] > 1 and r["type"] != "chain":
            problems.append("brand")

        points.append({
            "pointCode": r["point_code"],
            "pointName": r["point_name"] or "—",
            "inn": r["inn"] or "",
            "status": r["status"],
            "type": r["type"],
            "days": r["days"] or "",
            "dayCount": r["day_count"],
            "problems": problems,
        })

    # Проблемные — вверх списка, внутри группы по алфавиту
    points.sort(key=lambda p: (not p["problems"], p["pointName"]))

    return {
        "success": True,
        "agent": agent,
        "points": points,
        "problemCount": sum(1 for p in points if p["problems"]),
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
                       COALESCE(status, 0) AS status
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
    agents = [
        {
            "agent": r["agent"],
            "days": r["days"] or "",
            "dayList": split_days(r["days"]),
            "dayCount": r["day_count"],
            "tooManyDays": r["day_count"] > MAX_VISIT_DAYS,
        }
        for r in rows
    ]

    # Конфликт по бренду считаем только внутри бренда открытого агента
    brand = agent_brand(agent) if agent else ""
    same_brand = [a for a in agents if agent_brand(a["agent"]) == brand] if brand else agents

    problems = []
    if any(a["tooManyDays"] for a in agents):
        problems.append("days")
    if point_type != "chain" and len(same_brand) > 1:
        problems.append("brand")

    return {
        "success": True,
        "pointCode": point_code,
        "pointName": (info["point_name"] if info else None) or (rows[0]["point_name"] if rows else "—"),
        "inn": (info["inn"] if info else "") or "",
        "type": point_type,
        "status": (info["status"] if info else 0),
        "agents": agents,
        "sameBrandAgents": same_brand,
        "problems": problems,
        "maxDays": MAX_VISIT_DAYS,
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
    if role not in ("supervisor", "admin"):
        return {"success": False, "message": "Недостаточно прав"}

    return {
        "success": True,
        "role": role,
        "isAdmin": role == "admin",
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


async def panel_start(login_value: str, password: str) -> dict:
    """Первый экран панели: супервайзеру — его агенты, админу — список супервайзеров."""
    auth = await _check_panel(login_value, password)
    if not auth.get("success"):
        return auth

    if auth["isAdmin"]:
        result = await list_supervisors()
        if result.get("success"):
            result.update({"isAdmin": True, "supervisor": ""})
        return result

    result = await list_agents(auth["supervisor"])
    if result.get("success"):
        result.update({"isAdmin": False, "brand": auth["brand"]})
    return result


async def panel_supervisors(login_value: str, password: str, search: str = "") -> dict:
    auth = await _check_panel(login_value, password)
    if not auth.get("success"):
        return auth
    if not auth["isAdmin"]:
        return {"success": False, "message": "Недостаточно прав"}
    return await list_supervisors(search)


async def panel_agents(login_value: str, password: str,
                       supervisor: str = "", search: str = "") -> dict:
    auth = await _check_panel(login_value, password)
    if not auth.get("success"):
        return auth
    target = supervisor if auth["isAdmin"] else auth["supervisor"]
    return await list_agents(target, search)


async def panel_agent_points(login_value: str, password: str, agent: str,
                             search: str = "") -> dict:
    auth = await _check_panel(login_value, password)
    if not auth.get("success"):
        return auth
    if not await _agent_allowed(auth, agent):
        return {"success": False, "message": "Этот агент не в вашем подчинении"}
    return await agent_points(agent, "", search)


async def panel_point_details(login_value: str, password: str,
                              point_code: str, agent: str = "") -> dict:
    auth = await _check_panel(login_value, password)
    if not auth.get("success"):
        return auth
    if agent and not await _agent_allowed(auth, agent):
        return {"success": False, "message": "Этот агент не в вашем подчинении"}
    return await point_details(point_code, agent)


async def panel_unattach_days(login_value: str, password: str, agent: str,
                              point_code: str, days: list) -> dict:
    auth = await _check_panel(login_value, password)
    if not auth.get("success"):
        return auth
    if not await _agent_allowed(auth, agent):
        return {"success": False, "message": "Этот агент не в вашем подчинении"}
    return await unattach_days(agent, point_code, days)


async def panel_unattach_agent(login_value: str, password: str, agent: str,
                               point_code: str) -> dict:
    auth = await _check_panel(login_value, password)
    if not auth.get("success"):
        return auth
    # Открепить чужого агента супервайзер может только на своей точке —
    # то есть там, где стоит кто-то из его подчинённых
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


async def panel_set_point_type(login_value: str, password: str,
                               point_code: str, point_type: str) -> dict:
    auth = await _check_panel(login_value, password)
    if not auth.get("success"):
        return auth
    return await set_point_type(point_code, point_type)


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


# Сколько дней визита агент может занять на одной точке
MAX_VISIT_DAYS = 3


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


async def check_attach_allowed(point_code: str, agent: str) -> dict:
    """
    Можно ли агенту прикрепиться к этой точке.

    Два правила:
      1. Точка занята другим агентом того же бренда — прикрепление запрещено
         (OR0104 блокирует OR0111, но не UL1111).
      2. У самого агента на точке не больше MAX_VISIT_DAYS дней суммарно.
         Если уже занято два дня, третий добавить можно, четвёртый — нет.

    Возвращает:
      allowed     — можно ли продолжать
      reason      — 'brand' | 'limit' | None
      blockedBy   — логин агента, занявшего точку (для reason='brand')
      myDays      — дни, которые агент уже занял на этой точке
      remaining   — сколько дней ещё можно выбрать
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
            # На сетевой точке несколько агентов одного бренда — норма
            point_type = await conn.fetchval(
                "SELECT COALESCE(type, 'def') FROM client_base WHERE point_code = $1",
                point_code,
            )
    except Exception as e:
        return _db_error(e)

    is_chain = point_type == "chain"

    my_days, other_agent = [], None
    for r in rows:
        row_agent = (r["agent"] or "").upper()
        if row_agent == (agent or "").upper():
            my_days.extend(split_days(r["visit_day"]))
        elif agent_brand(row_agent) == brand and brand and not is_chain:
            # Точку уже занял коллега по бренду (на сетевой это разрешено)
            other_agent = other_agent or row_agent

    # Один и тот же день мог попасть в две записи — считаем уникальные
    my_days = list(dict.fromkeys(my_days))

    if other_agent:
        return {
            "success": True, "allowed": False, "reason": "brand",
            "blockedBy": other_agent, "myDays": my_days,
            "remaining": 0,
        }

    remaining = MAX_VISIT_DAYS - len(my_days)
    if remaining <= 0:
        return {
            "success": True, "allowed": False, "reason": "limit",
            "blockedBy": None, "myDays": my_days, "remaining": 0,
        }

    return {
        "success": True, "allowed": True, "reason": None,
        "blockedBy": None, "myDays": my_days, "remaining": remaining,
    }


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

async def attach(agent: str, point_code: str, point_name: str, visit_day: str) -> dict:
    """
    Прикрепляет точку к агенту.

    Правила проверяются ЗДЕСЬ, а не только в интерфейсе: бот и сайт — это
    два независимых клиента, и любой из них может отправить запрос с лишними
    днями (так и случилось: сайт ограничивал выбор занятых дней, но не их
    количество, и у агента набралось пять дней вместо трёх).
    """
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
        if check.get("reason") == "brand":
            return {"success": False,
                    "message": f"В этой точке закреплён другой агент вашего бренда "
                               f"({check.get('blockedBy')})"}
        return {"success": False,
                "message": f"У вас уже {MAX_VISIT_DAYS} дня в этой точке: "
                           f"{', '.join(check.get('myDays', []))}"}

    my_days = check.get("myDays", [])
    remaining = check.get("remaining", MAX_VISIT_DAYS)

    duplicates = [d for d in days if d in my_days]
    if duplicates:
        return {"success": False,
                "message": f"Эти дни у вас уже заняты на этой точке: {', '.join(duplicates)}"}

    if len(days) > remaining:
        return {"success": False,
                "message": f"Можно выбрать ещё {remaining}, а выбрано {len(days)}. "
                           f"Всего на одну точку — не больше {MAX_VISIT_DAYS} дней."}

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

    return {"success": True, "message": "Точка успешно прикреплена к вам!"}


# ======================================================================
# ДОБАВЛЕНИЕ НОВОЙ ТОЧКИ
# ======================================================================

async def add_tt(data: dict) -> dict:
    """
    data приходит из bot.py в тех же ключах, что раньше уходили в Apps Script
    (clientName, deliveryCode, visitDay), поэтому здесь они раскладываются
    по колонкам таблицы add_requests.
    """
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

    return {"success": True, "message": "Новая торговая точка успешно добавлена!"}
