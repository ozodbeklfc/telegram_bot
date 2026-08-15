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
                "SELECT login FROM users WHERE login = $1 AND password = $2",
                login_value,
                password,
            )
    except Exception as e:
        return _db_error(e)

    if row is None:
        return {"success": False, "message": "Неверный логин или пароль"}

    return {"success": True, "message": "Успешный вход!", "user": {"login": row["login"]}}


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
    Бренд агента — буквенная часть логина: OR0104 → OR, BAH001 → BAH.

    Раньше брались ровно два первых символа, но в базе есть агенты с
    трёхбуквенным префиксом (BAH001), и у них бренд получался «BA» —
    тогда BAH001 и BAN001 считались бы коллегами по бренду.
    """
    m = re.match(r"^[A-Za-z]+", (agent or "").strip())
    return m.group(0).upper() if m else ""


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
    brand = agent_brand(agent)

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO attachments
                    (point_code, point_name, agent_brand, agent, visit_day)
                VALUES ($1, $2, $3, $4, $5)
                """,
                point_code, point_name, brand, agent, visit_day,
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
