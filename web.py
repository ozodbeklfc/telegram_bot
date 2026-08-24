"""
HTTP-сервис для сайта (index.html).

Браузер не умеет ходить в Postgres напрямую — раньше посредником был
Google Apps Script. Этот модуль встаёт на его место: принимает те же
POST-запросы с полем "action" и отвечает тем же JSON. Поэтому в index.html
достаточно поменять одну строку — адрес WEB_APP_URL.

Логика не дублируется: все запросы уходят в api.py, тот же самый, что
использует бот, и пул соединений с базой у них общий.

Запускается вместе с ботом одним процессом (см. main() в bot.py).
"""

import logging
import os
from pathlib import Path

from aiohttp import web

import api
import notify

logger = logging.getLogger(__name__)

# Папка, где лежит index.html (рядом с этим файлом)
STATIC_DIR = Path(__file__).parent

# Заголовки, разрешающие браузеру обращаться к сервису с другого адреса.
# Нужны, если сайт открывается не с этого же домена (например, с GitHub Pages
# или как Telegram Mini App).
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


async def handle_options(request: web.Request) -> web.Response:
    """Предварительный CORS-запрос браузера перед POST."""
    return web.Response(status=204, headers=CORS_HEADERS)


async def handle_admin(request: web.Request) -> web.Response:
    """Панель супервайзера — отдельная страница, свой вход."""
    admin_file = STATIC_DIR / "admin.html"
    if not admin_file.exists():
        return web.Response(text="admin.html не найден рядом с web.py", status=404)
    return web.FileResponse(admin_file)


async def handle_index(request: web.Request) -> web.Response:
    """Отдаёт сам сайт, если он лежит рядом с ботом."""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return web.Response(text="index.html не найден рядом с web.py", status=404)
    return web.FileResponse(index_file)


async def handle_api(request: web.Request) -> web.Response:
    """
    Единая точка входа, как было у Apps Script: действие определяется
    полем "action" внутри тела запроса.

    Сайт отправляет Content-Type: text/plain, поэтому разбираем тело
    вручную через json(), не полагаясь на заголовок.
    """
    try:
        payload = await request.json()
    except Exception:
        return web.json_response(
            {"success": False, "message": "Некорректный запрос"},
            status=400, headers=CORS_HEADERS,
        )

    action = payload.get("action")

    try:
        if action == "login":
            result = await api.login(payload.get("login", ""), payload.get("password", ""))

        elif action == "check_inn":
            result = await api.check_inn(payload.get("inn", ""))

        elif action == "attach":
            result = await api.attach(
                agent=payload.get("agent", ""),
                point_code=payload.get("pointCode", ""),
                point_name=payload.get("pointName", ""),
                visit_day=payload.get("visitDay", ""),
            )
            if result.get("success"):
                await notify.notify_admin(notify.build_attach_text(payload))

        elif action == "add_tt":
            result = await api.add_tt(payload)
            if result.get("success"):
                await notify.notify_admin(notify.build_add_text(payload))

        elif action == "panel_start":
            result = await api.panel_start(
                payload.get("login", ""), payload.get("password", ""))

        elif action == "panel_supervisors":
            result = await api.panel_supervisors(
                payload.get("login", ""), payload.get("password", ""),
                payload.get("search", ""))

        elif action == "panel_agents":
            result = await api.panel_agents(
                payload.get("login", ""), payload.get("password", ""),
                payload.get("supervisor", ""), payload.get("search", ""))

        elif action == "panel_agent_points":
            result = await api.panel_agent_points(
                payload.get("login", ""), payload.get("password", ""),
                payload.get("agent", ""), payload.get("search", ""))

        elif action == "panel_point_details":
            result = await api.panel_point_details(
                payload.get("login", ""), payload.get("password", ""),
                payload.get("pointCode", ""), payload.get("agent", ""))

        elif action == "panel_unattach_days":
            result = await api.panel_unattach_days(
                payload.get("login", ""), payload.get("password", ""),
                payload.get("agent", ""), payload.get("pointCode", ""),
                payload.get("days", []))

        elif action == "panel_unattach_agent":
            result = await api.panel_unattach_agent(
                payload.get("login", ""), payload.get("password", ""),
                payload.get("agent", ""), payload.get("pointCode", ""))

        elif action == "panel_set_point_type":
            result = await api.panel_set_point_type(
                payload.get("login", ""), payload.get("password", ""),
                payload.get("pointCode", ""), payload.get("type", ""))

        elif action == "check_attach":
            result = await api.check_attach_allowed(
                payload.get("pointCode", ""), payload.get("agent", "")
            )

        elif action == "search_similar":
            result = await api.search_similar_points(payload.get("name", ""))

        elif action == "change_password":
            result = await api.change_password(
                login_value=payload.get("login", ""),
                current_password=payload.get("currentPassword", ""),
                new_password=payload.get("newPassword", ""),
            )
            if result.get("success"):
                await notify.notify_admin(
                    notify.build_password_changed_text(payload.get("login", ""))
                )

        else:
            result = {"success": False, "message": f"Неизвестное действие: {action}"}

    except Exception:
        # api.py и так не бросает исключения наружу, но если что-то всё же
        # прорвётся — сайт получит понятный JSON, а не пустой ответ с 500.
        logger.exception("Необработанная ошибка при выполнении действия %s", action)
        result = {"success": False, "message": "Внутренняя ошибка сервера"}

    return web.json_response(result, headers=CORS_HEADERS)


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/admin", handle_admin)
    app.router.add_post("/", handle_api)
    app.router.add_post("/api", handle_api)      # запасной путь
    app.router.add_options("/", handle_options)
    app.router.add_options("/api", handle_options)
    return app


async def start_web_server():
    """
    Поднимает сервер и НЕ блокирует выполнение — бот продолжает работать
    в том же процессе. Возвращает runner, чтобы его можно было корректно
    остановить при завершении.
    """
    port = int(os.getenv("PORT", "8080"))

    runner = web.AppRunner(create_app())
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info("Веб-сервис для сайта запущен на порту %s", port)
    return runner


if __name__ == "__main__":
    # Отдельный запуск, без бота — удобно для локальной проверки
    logging.basicConfig(level=logging.INFO)
    web.run_app(create_app(), port=int(os.getenv("PORT", "8080")))
