"""
Уведомления администратору в Telegram.

Используется веб-сервисом (web.py): заявки, отправленные с сайта, должны
приходить админу так же, как приходят заявки из бота.

Отправка идёт напрямую через HTTP API Telegram, без aiogram — чтобы web.py
не пришлось импортировать bot.py (это создало бы круговой импорт, ведь
bot.py сам импортирует web.py).

Ошибка отправки никогда не ломает основной сценарий: заявка уже записана
в базу, и пользователь не должен видеть ошибку из-за проблем с уведомлением.
"""

import logging

import aiohttp

from config import BOT_TOKEN, ADMIN_ID

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
SEND_TIMEOUT = 10


async def notify_admin(text: str):
    """Отправляет админу сообщение. Молча пропускает, если ADMIN_ID не задан."""
    if not ADMIN_ID:
        return

    try:
        timeout = aiohttp.ClientTimeout(total=SEND_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                TELEGRAM_API,
                json={"chat_id": ADMIN_ID, "text": text},
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("Telegram отклонил уведомление (%s): %s", resp.status, body[:200])
    except Exception:
        logger.exception("Не удалось отправить уведомление админу")


def build_attach_text(payload: dict) -> str:
    """Карточка прикрепления — в том же виде, в каком её присылает бот."""
    return (
        "📎 НОВАЯ ЗАЯВКА: ПРИКРЕПЛЕНИЕ ТОЧКИ\n"
        "🌐 Источник: сайт\n\n"
        "📋 Данные прикрепления:\n\n"
        f"🏪 Точка: {payload.get('pointName')}\n"
        f"🔢 Код: {payload.get('pointCode')}\n"
        f"👤 Агент: {payload.get('agent')}\n"
        f"📅 Дни визита: {payload.get('visitDay')}"
    )


def build_add_text(payload: dict) -> str:
    """Карточка новой точки — в том же виде, в каком её присылает бот."""
    geo = payload.get("geo") or "—"
    return (
        "📥 НОВАЯ ЗАЯВКА: ДОБАВЛЕНИЕ ТТ\n"
        "🌐 Источник: сайт\n\n"
        "📋 Данные новой торговой точки:\n\n"
        f"🏢 Клиент: {payload.get('clientName')}\n"
        f"📍 Геометка: {geo}\n"
        f"📍 Адрес: {payload.get('address')}\n"
        f"📞 Телефон: {payload.get('phone')}\n"
        f"🧾 ИНН: {payload.get('inn')}\n\n"
        f"🌍 Регион: {payload.get('region')}\n"
        f"🏙 Область: {payload.get('oblast')}\n"
        f"🏘 Округ: {payload.get('okrug')}\n"
        f"📌 Район: {payload.get('rayon')}\n"
        f"🏪 Формат: {payload.get('format')}\n"
        f"🔀 Канал: {payload.get('channel')}\n"
        f"🏷 Тип: {payload.get('type')}\n"
        f"⭐ Категория: {payload.get('category')}\n"
        f"🚚 Код доставщика: {payload.get('deliveryCode')}\n\n"
        f"👤 Агент: {payload.get('agent')}\n"
        f"📅 Дни визита: {payload.get('visitDay')}\n"
        f"💬 Комментарий: {payload.get('comments') or '-'}"
    )


def build_password_changed_text(login_value: str) -> str:
    # Сам пароль в уведомление не попадает — только факт смены
    return f"🔑 Агент {login_value.upper()} сменил пароль (через сайт)."
