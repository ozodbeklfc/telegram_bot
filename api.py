import aiohttp
import asyncio
import json
import logging

from config import WEB_APP_URL

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
TIMEOUT_SECONDS = 30
RETRY_DELAY_SECONDS = 2

_session: aiohttp.ClientSession | None = None


async def get_session() -> aiohttp.ClientSession:
    """
    Переиспользуемая HTTP-сессия вместо создания новой (с новым TCP/TLS
    хендшейком) на каждый запрос. Важно при нескольких пользователях,
    работающих одновременно — соединения переиспользуются из пула.
    """
    global _session
    if _session is None or _session.closed:
        connector = aiohttp.TCPConnector(limit=50)
        _session = aiohttp.ClientSession(connector=connector)
    return _session


async def call_script(payload: dict) -> dict:
    """
    Отправляет запрос в тот же Google Apps Script, который уже используется
    Mini App'ом (doPost). Формат запроса и ответа не меняется.

    Google Apps Script иногда "тормозит" (холодный старт / нагрузка) и либо
    не успевает ответить за разумное время, либо возвращает пустое тело
    вместо JSON. В обоих случаях — это временный сбой, поэтому делаем
    несколько повторных попыток перед тем, как вернуть ошибку пользователю.

    payload обязательно содержит "action": "login" | "check_inn" | "attach" | "add_tt"
    """
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            session = await get_session()
            async with session.post(
                WEB_APP_URL,
                json=payload,
                headers={"Content-Type": "text/plain;charset=utf-8"},
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
            ) as resp:
                text = await resp.text()

            if not text.strip():
                raise ValueError("Google Apps Script вернул пустой ответ")

            return json.loads(text)

        except Exception as e:
            last_error = e
            logger.warning(
                "Попытка %s/%s обращения к Google Apps Script не удалась: %s",
                attempt, MAX_ATTEMPTS, e,
            )
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(RETRY_DELAY_SECONDS)

    logger.exception("Все попытки обращения к Google Apps Script исчерпаны")
    return {
        "success": False,
        "message": f"Google Таблица сейчас не отвечает ({last_error}). Попробуйте ещё раз через минуту.",
    }


async def login(login_value: str, password: str) -> dict:
    return await call_script({"action": "login", "login": login_value, "password": password})


async def check_inn(inn: str) -> dict:
    return await call_script({"action": "check_inn", "inn": inn})


async def attach(agent: str, point_code: str, point_name: str, visit_day: str) -> dict:
    return await call_script({
        "action": "attach",
        "agent": agent,
        "pointCode": point_code,
        "pointName": point_name,
        "visitDay": visit_day,
    })


async def add_tt(data: dict) -> dict:
    payload = {"action": "add_tt"}
    payload.update(data)
    return await call_script(payload)
