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
                       END AS brand
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


async def list_agents(brand: str, search: str = "") -> dict:
    """
    Агенты одного бренда со сводкой по прикреплениям.

    Список берётся объединением двух источников: таблицы пользователей
    (кто может войти) и таблицы прикреплений (кто реально работает на
    точках). Второе важно — в выгрузке встречаются агенты, которых ещё
    не завели в users, и без них картина была бы неполной.
    """
    brand = (brand or "").strip().upper()[:2]
    if not brand:
        return {"success": False, "message": "Не указан бренд"}

    pattern = f"%{(search or '').strip().upper()}%"

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH agents AS (
                    SELECT upper(login) AS agent FROM users
                     WHERE COALESCE(brand, upper(left(login, 2))) = $1
                       AND COALESCE(role, 'agent') = 'agent'
                    UNION
                    SELECT upper(agent) AS agent FROM attachments
                     WHERE agent IS NOT NULL AND upper(left(agent, 2)) = $1
                )
                SELECT a.agent,
                       count(DISTINCT t.point_code) AS points,
                       count(t.id)                  AS days
                  FROM agents a
                  LEFT JOIN attachments t ON upper(t.agent) = a.agent
                 WHERE a.agent LIKE $2
                 GROUP BY a.agent
                 ORDER BY points DESC, a.agent
                """,
                brand, pattern,
            )
    except Exception as e:
        return _db_error(e)

    return {
        "success": True,
        "brand": brand,
        "agents": [
            {"agent": r["agent"], "points": r["points"], "days": r["days"]}
            for r in rows
        ],
    }


async def agent_points(agent: str, brand: str = "", search: str = "") -> dict:
    """
    Точки, на которых стоит агент.

    Дни визита собираются в один список: в базе каждый день — отдельная
    строка, а админу удобнее видеть точку одной карточкой.
    """
    agent = (agent or "").strip().upper()
    if not agent:
        return {"success": False, "message": "Не указан агент"}

    # Админ видит только свой бренд — проверяем, а не полагаемся на интерфейс
    if brand and agent[:2] != brand.strip().upper()[:2]:
        return {"success": False, "message": "Агент относится к другому бренду"}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT t.point_code,
                       COALESCE(max(c.point_name), max(t.point_name)) AS point_name,
                       max(c.inn)                                     AS inn,
                       max(c.status)                                  AS status,
                       string_agg(DISTINCT t.visit_day, ', ')          AS days,
                       count(*)                                       AS day_count,
                       min(t.created_at)                              AS created_at
                  FROM attachments t
             LEFT JOIN client_base c ON c.point_code = t.point_code
                 WHERE upper(t.agent) = $1
                 GROUP BY t.point_code
                HAVING $2 = ''
                    OR upper(COALESCE(max(c.point_name), max(t.point_name))) LIKE $2
                    OR t.point_code LIKE $2
                    OR max(c.inn) LIKE $2
                 ORDER BY point_name
                """,
                agent,
                f"%{search.strip().upper()}%" if (search or "").strip() else "",
            )
    except Exception as e:
        return _db_error(e)

    return {
        "success": True,
        "agent": agent,
        "points": [
            {
                "pointCode": r["point_code"],
                "pointName": r["point_name"] or "—",
                "inn": r["inn"] or "",
                "status": r["status"] or 0,
                "days": r["days"] or "",
                "dayCount": r["day_count"],
                "createdAt": r["created_at"].isoformat() if r["created_at"] else "",
            }
            for r in rows
        ],
    }


async def _check_admin(login_value: str, password: str) -> dict:
    """
    Проверяет, что запрос пришёл от админа, и возвращает его бренд ИЗ БАЗЫ.

    Бренд намеренно не берётся из запроса: иначе админ UL, подставив в
    запрос "OR", увидел бы чужих агентов.
    """
    auth = await login(login_value, password)
    if not auth.get("success"):
        return {"success": False, "message": "Неверный логин или пароль"}
    if auth.get("role") != "admin":
        return {"success": False, "message": "Недостаточно прав"}

    # Пустой бренд = общий админ: видит все бренды и выбирает нужный сам.
    # У брендового админа бренд жёстко задан в базе и подменить его нельзя.
    return {
        "success": True,
        "brand": auth.get("brand") or "",
        "allBrands": not auth.get("brand"),
        "login": login_value,
    }


async def admin_brands(login_value: str, password: str) -> dict:
    """Первый шаг общего админа: какие бренды вообще есть."""
    auth = await _check_admin(login_value, password)
    if not auth.get("success"):
        return auth

    if not auth["allBrands"]:
        # Админ закреплён за одним брендом — выбирать не из чего
        return {"success": True, "allBrands": False, "brand": auth["brand"], "brands": []}

    result = await list_brands()
    if result.get("success"):
        result["allBrands"] = True
        result["brand"] = ""
    return result


async def admin_list_agents(login_value: str, password: str,
                            brand: str = "", search: str = "") -> dict:
    auth = await _check_admin(login_value, password)
    if not auth.get("success"):
        return auth

    # Брендовому админу бренд из запроса не подставить — берём из базы
    target = brand if auth["allBrands"] else auth["brand"]
    if not target:
        return {"success": False, "message": "Не выбран бренд"}

    return await list_agents(target, search)


async def admin_agent_points(login_value: str, password: str, agent: str,
                             search: str = "") -> dict:
    auth = await _check_admin(login_value, password)
    if not auth.get("success"):
        return auth

    # Общий админ смотрит любого агента, брендовый — только своего
    return await agent_points(agent, "" if auth["allBrands"] else auth["brand"], search)


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
    except Exception as e:
        return _db_error(e)

    my_days, other_agent = [], None
    for r in rows:
        row_agent = (r["agent"] or "").upper()
        if row_agent == (agent or "").upper():
            my_days.extend(split_days(r["visit_day"]))
        elif agent_brand(row_agent) == brand and brand:
            # Точку уже занял коллега по бренду
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
