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

import asyncpg

from config import DATABASE_URL

logger = logging.getLogger(__name__)

POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 10
COMMAND_TIMEOUT = 15  # секунд на один запрос

_pool: asyncpg.Pool | None = None


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

async def check_inn(inn: str) -> dict:
    inn = (inn or "").strip()

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT point_code, point_name FROM client_base WHERE inn = $1",
                inn,
            )
    except Exception as e:
        return _db_error(e)

    if row is None:
        return {"success": True, "exists": False}

    return {
        "success": True,
        "exists": True,
        "pointCode": row["point_code"],
        "pointName": row["point_name"],
    }


# ======================================================================
# ПРИКРЕПЛЕНИЕ ТОЧКИ
# ======================================================================

async def attach(agent: str, point_code: str, point_name: str, visit_day: str) -> dict:
    # Бренд агента — первые два символа логина, как это делалось в Apps Script
    agent_brand = (agent or "")[:2].upper()

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO attachments
                    (point_code, point_name, agent_brand, agent, visit_day)
                VALUES ($1, $2, $3, $4, $5)
                """,
                point_code, point_name, agent_brand, agent, visit_day,
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