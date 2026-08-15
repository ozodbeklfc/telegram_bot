import asyncio
import logging
import re

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardRemove, ReplyKeyboardMarkup, BotCommand,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from config import BOT_TOKEN, ADMIN_ID
from states import AuthStates, InnStates, AttachStates, AddStates, ChangePasswordStates
import api
import data
from keyboards import build_paginated_keyboard, build_days_keyboard, confirm_keyboard
from web import start_web_server

logging.basicConfig(level=logging.INFO)

router = Router()

INN_REGEX = re.compile(r"^\d{9}$|^\d{14}$")
PHONE_REGEX = re.compile(r"^\+998\d{9}$")
STOPWORDS_REGEX = re.compile(r"\b(OOO|MCHJ|YATT|XK|ООО|МЧЖ|ЯТТ)\b")

# Требования к новому паролю — можно менять здесь
MIN_PASSWORD_LENGTH = 4
MAX_PASSWORD_LENGTH = 32


def get_items_for(field: str, fsm_data: dict) -> list[str]:
    """Пересобирает список вариантов для поля на основе уже выбранных родительских значений."""
    if field == "region":
        return data.REGIONS
    if field == "oblast":
        return data.OBLAST.get(fsm_data.get("region"), [])
    if field == "okrug":
        return data.OKRUG.get(fsm_data.get("oblast"), [])
    if field == "rayon":
        # Район теперь зависит от ОКРУГА, а не от области
        return data.get_rayons(fsm_data.get("okrug", ""))
    if field == "format":
        return data.FORMAT
    if field == "channel":
        return data.CHANNEL
    if field == "type":
        return data.TYPE
    if field == "category":
        return data.CATEGORY
    if field == "delivery":
        return data.get_delivery_codes(fsm_data.get("oblast", ""))
    return []


def clean_client_name(raw: str) -> str:
    val = raw.upper()
    val = STOPWORDS_REGEX.sub("", val)
    val = re.sub(r"[^A-Z0-9\s]", "", val)
    val = re.sub(r"\s{2,}", " ", val)
    return val.lstrip()


async def safe_answer(callback: CallbackQuery, *args, **kwargs):
    """
    Обёртка над callback.answer(). Если запрос "протух" (пользователь ждал
    слишком долго, например пока шёл запрос к Google Apps Script) — Telegram
    возвращает ошибку "query is too old". Она безобидна (просто не снимется
    иконка загрузки на кнопке) и не должна ронять весь обработчик.
    """
    try:
        await callback.answer(*args, **kwargs)
    except Exception:
        logging.warning("Не удалось ответить на callback (устарел) — игнорирую")


async def delete_message_safe(message: Message):
    """
    Удаляет сообщение пользователя (используется для сообщений с паролями,
    чтобы они не висели в истории чата). Если удалить не вышло — например,
    прошло больше 48 часов или у бота нет прав — молча продолжаем работу.
    """
    try:
        await message.delete()
    except Exception:
        logging.warning("Не удалось удалить сообщение с паролем — игнорирую")


async def ask_inn(message: Message, state: FSMContext):
    await state.set_state(InnStates.waiting_inn)
    await message.answer(
        "🔎 Введите ИНН точки (строго 9 или 14 цифр):",
        reply_markup=ReplyKeyboardRemove(),
    )


# ======================================================================
# СТАРТ / ОТМЕНА / НАЗАД
# ======================================================================

@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AuthStates.waiting_login)
    await message.answer(
        "👋 Добро пожаловать!\n\nВведите ваш логин агента (например: OR0111):",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):
    current_data = await state.get_data()
    agent = current_data.get("agent")
    if agent:
        await state.set_data({"agent": agent})
        await ask_inn(message, state)
    else:
        await state.clear()
        await message.answer("Действие отменено. Наберите /start, чтобы начать заново.")


# ======================================================================
# СМЕНА ПАРОЛЯ: точка входа
#
# ВАЖНО: этот хендлер должен стоять ВЫШЕ хендлеров, привязанных к
# состояниям (process_inn, add_address и т.д.). aiogram проверяет
# хендлеры в порядке объявления, и хендлер состояния перехватил бы
# команду как обычный текст — ровно так /change_password однажды
# уехал в проверку ИНН.
# ======================================================================

@router.message(Command("change_password"))
async def change_password_start(message: Message, state: FSMContext):
    fsm_data = await state.get_data()
    agent = fsm_data.get("agent")

    if not agent:
        await message.answer("🔒 Сначала войдите в систему — наберите /start.")
        return

    current_state = await state.get_state()
    if current_state != InnStates.waiting_inn.state:
        # Не даём начать смену пароля посреди заполнения формы, иначе
        # весь введённый прогресс придётся выбрасывать.
        await message.answer(
            "⚠️ Сейчас идёт незавершённый сценарий.\n\n"
            "Доведите его до конца или наберите /cancel, а затем повторите "
            "/change_password."
        )
        return

    await state.set_state(ChangePasswordStates.waiting_current)
    await message.answer(
        f"🔑 Смена пароля для агента {agent}.\n\n"
        f"Введите ваш ТЕКУЩИЙ пароль:\n\n"
        f"(/cancel — отменить)"
    )


# Порядок шагов для команды /back — по этим спискам ищем "предыдущий" шаг.
STEP_ORDER = {
    "auth": [AuthStates.waiting_login, AuthStates.waiting_password],
    "add": [
        AddStates.waiting_client_name, AddStates.confirm_similar,
        AddStates.waiting_geo, AddStates.waiting_address,
        AddStates.waiting_phone, AddStates.choosing_region, AddStates.choosing_oblast,
        AddStates.choosing_okrug, AddStates.choosing_rayon, AddStates.choosing_format,
        AddStates.choosing_channel, AddStates.choosing_type, AddStates.choosing_category,
        AddStates.choosing_delivery, AddStates.choosing_days, AddStates.waiting_comments,
        AddStates.confirm,
    ],
    "attach": [AttachStates.choosing_days, AttachStates.confirm],
}


async def render_step(target_state, message: Message, state: FSMContext):
    """Заново показывает вопрос/клавиатуру для указанного шага (используется командой /back)."""
    fsm_data = await state.get_data()
    s = target_state.state

    if s == AuthStates.waiting_login.state:
        await message.answer("Введите ваш логин агента:")
    elif s == AuthStates.waiting_password.state:
        await message.answer("Введите пароль:")
    elif s == AddStates.waiting_client_name.state:
        await message.answer(
            "1️⃣ Введите название клиента (строго латиницей, например: SUPERMARKET MAX):",
            reply_markup=ReplyKeyboardRemove(),
        )
    elif s == AddStates.confirm_similar.state:
        matches = fsm_data.get("similar_matches", [])
        await message.answer(
            build_similar_text(fsm_data.get("clientName", ""), matches),
            reply_markup=build_similar_keyboard(matches),
        )
    elif s == AddStates.waiting_geo.state:
        await message.answer(
            "2️⃣ Отправьте геолокацию точки:\n\n"
            "Нажмите на значок 📎 (скрепка) рядом с полем ввода → «Геопозиция» — "
            "там можно передвинуть булавку и выбрать нужную точку на карте, "
            "затем нажмите «Отправить геопозицию».",
            reply_markup=ReplyKeyboardRemove(),
        )
    elif s == AddStates.waiting_address.state:
        await message.answer("3️⃣ Введите фактический адрес доставки:", reply_markup=ReplyKeyboardRemove())
    elif s == AddStates.waiting_phone.state:
        await message.answer("4️⃣ Введите номер телефона (например: +998901234567):")
    elif s == AddStates.choosing_region.state:
        await message.answer("5️⃣ Выберите регион:", reply_markup=build_paginated_keyboard(data.REGIONS, "region"))
    elif s == AddStates.choosing_oblast.state:
        region = fsm_data.get("region")
        await message.answer(
            f"Регион: {region}\n\n6️⃣ Выберите область:",
            reply_markup=build_paginated_keyboard(data.OBLAST.get(region, []), "oblast"),
        )
    elif s == AddStates.choosing_okrug.state:
        oblast = fsm_data.get("oblast")
        await message.answer(
            f"Область: {oblast}\n\n7️⃣ Выберите округ:",
            reply_markup=build_paginated_keyboard(data.OKRUG.get(oblast, []), "okrug"),
        )
    elif s == AddStates.choosing_rayon.state:
        okrug = fsm_data.get("okrug")
        await message.answer(
            f"Округ: {okrug}\n\n8️⃣ Выберите район:",
            reply_markup=build_paginated_keyboard(data.get_rayons(okrug), "rayon"),
        )
    elif s == AddStates.choosing_format.state:
        await message.answer("9️⃣ Выберите формат:", reply_markup=build_paginated_keyboard(data.FORMAT, "format"))
    elif s == AddStates.choosing_channel.state:
        await message.answer("🔟 Выберите канал:", reply_markup=build_paginated_keyboard(data.CHANNEL, "channel"))
    elif s == AddStates.choosing_type.state:
        await message.answer("1️⃣1️⃣ Выберите тип:", reply_markup=build_paginated_keyboard(data.TYPE, "type"))
    elif s == AddStates.choosing_category.state:
        await message.answer("1️⃣2️⃣ Выберите категорию:", reply_markup=build_paginated_keyboard(data.CATEGORY, "category"))
    elif s == AddStates.choosing_delivery.state:
        delivery_list = data.get_delivery_codes(fsm_data.get("oblast", ""))
        await message.answer("1️⃣3️⃣ Выберите код доставщика:", reply_markup=build_paginated_keyboard(delivery_list, "delivery"))
    elif s == AddStates.choosing_days.state:
        await message.answer(
            "1️⃣4️⃣ Выберите дни визита (можно до 3):",
            reply_markup=build_days_keyboard(fsm_data.get("visit_days", [])),
        )
    elif s == AddStates.waiting_comments.state:
        await message.answer("1️⃣5️⃣ Введите комментарий (если его нет — отправьте символ «-»):")
    elif s == AddStates.confirm.state:
        await send_add_summary(message, state)
    elif s == AttachStates.choosing_days.state:
        await message.answer(
            "Выберите дни визита (можно до 3):",
            reply_markup=build_days_keyboard(fsm_data.get("visit_days", [])),
        )
    elif s == AttachStates.confirm.state:
        await send_attach_summary(message, state)


@router.message(Command("back"))
async def back_handler(message: Message, state: FSMContext):
    current = await state.get_state()
    if current is None:
        await message.answer("Нет активного сценария. Наберите /start.")
        return

    if current == InnStates.waiting_inn.state:
        await message.answer("Вы уже в начале сценария (ввод ИНН). Чтобы вернуться к логину — /start.")
        return

    for flow_name, steps in STEP_ORDER.items():
        state_values = [st.state for st in steps]
        if current in state_values:
            idx = state_values.index(current)
            if idx == 0:
                if flow_name in ("add", "attach"):
                    fsm_data = await state.get_data()
                    agent = fsm_data.get("agent")
                    if agent:
                        await state.set_data({"agent": agent})
                        await ask_inn(message, state)
                    else:
                        await state.clear()
                        await message.answer("Наберите /start, чтобы начать заново.")
                else:
                    await message.answer("Это первый шаг — назад некуда. /start для начала заново.")
                return
            prev = steps[idx - 1]
            await state.set_state(prev)
            await render_step(prev, message, state)
            return

    await message.answer("Команда /back недоступна на этом шаге.")


# ======================================================================
# АВТОРИЗАЦИЯ
# ======================================================================

@router.message(AuthStates.waiting_login)
async def process_login(message: Message, state: FSMContext):
    login_value = message.text.strip()
    await state.update_data(login=login_value)
    await state.set_state(AuthStates.waiting_password)
    await message.answer("Введите пароль:")


@router.message(AuthStates.waiting_password)
async def process_password(message: Message, state: FSMContext):
    password = message.text.strip()
    fsm_data = await state.get_data()
    login_value = fsm_data.get("login")

    checking_msg = await message.answer("⏳ Проверяю логин и пароль...")
    result = await api.login(login_value, password)

    if result.get("success"):
        await state.set_data({"agent": login_value.upper()})
        await checking_msg.edit_text(
            f"✅ Успешный вход!\n\n👤 Агент: {login_value}\n\n"
            f"🔑 Сменить пароль — команда /change_password"
        )
        await delete_message_safe(message)  # убираем пароль из переписки
        await ask_inn(message, state)
    else:
        await state.set_state(AuthStates.waiting_login)
        error_text = result.get("message", "Неверный логин или пароль")
        await checking_msg.edit_text(f"❌ {error_text}\n\nПопробуйте ещё раз. Введите логин агента:")


# ======================================================================
# ПРОВЕРКА ИНН
# ======================================================================

@router.message(InnStates.waiting_inn)
async def process_inn(message: Message, state: FSMContext):
    inn_value = message.text.strip()

    if not INN_REGEX.match(inn_value):
        await message.answer("⚠️ ИНН должен содержать ровно 9 или 14 цифр. Попробуйте ещё раз:")
        return

    checking_msg = await message.answer("⏳ Ищем точку в базе...")
    result = await api.check_inn(inn_value)

    if not result.get("success"):
        await checking_msg.edit_text(f"❌ {result.get('message', 'Ошибка при поиске ИНН')}")
        return

    if result.get("exists"):
        points = result.get("points") or [{
            "pointCode": result.get("pointCode"),
            "pointName": result.get("pointName"),
            "status": result.get("status", 0),
        }]
        active = [p for p in points if p.get("status") != 1]

        if not active:
            # Все коды этой точки пассивные
            first = points[0]
            await checking_msg.edit_text(
                f"🔴 ЭТА ТОЧКА ПАССИВНАЯ\n\n"
                f"🏪 {first['pointName']}\n"
                f"🧾 ИНН: {inn_value}\n\n"
                f"Обратитесь в финансовый отдел.\n\n"
                f"Можете ввести другой ИНН:"
            )
            return

        if len(active) > 1:
            # У одного ИНН несколько кодов контрагента (KALINA, UNILEVER,
            # NIVEA...) — какой именно прикреплять, решает агент
            await state.update_data(inn_points=active)
            await state.set_state(AttachStates.choosing_point)
            await checking_msg.edit_text(
                build_points_text(inn_value, active),
                reply_markup=build_points_keyboard(active),
            )
            return

        result = {**result, **active[0]}

        if result.get("status") == 1:
            # Пассивная точка: работать с ней агент не может, отправляем
            # обратно к вводу ИНН — состояние не меняем
            await checking_msg.edit_text(
                f"🔴 ЭТА ТОЧКА ПАССИВНАЯ\n\n"
                f"🏪 {result.get('pointName')}\n"
                f"🧾 ИНН: {inn_value}\n\n"
                f"Обратитесь в финансовый отдел.\n\n"
                f"Можете ввести другой ИНН:"
            )
            return

        await state.update_data(
            point_code=result.get("pointCode"),
            point_name=result.get("pointName"),
            visit_days=[],
        )
        await state.set_state(AttachStates.choosing_days)
        await checking_msg.edit_text(
            f"✅ Точка найдена в базе!\n\n"
            f"🏪 Название: {result.get('pointName')}\n"
            f"🔢 Код: {result.get('pointCode')}\n\n"
            f"Выберите дни визита (можно до 3):"
        )
        await message.answer("👇", reply_markup=build_days_keyboard([]))
    else:
        await state.update_data(inn=inn_value)
        await state.set_state(AddStates.waiting_client_name)
        await checking_msg.edit_text(
            "ℹ️ Точка с таким ИНН не найдена. Начинаем регистрацию новой точки.\n\n"
            "1️⃣ Введите название клиента (строго латиницей, например: SUPERMARKET MAX):"
        )


# ======================================================================
# СЦЕНАРИЙ "ДОБАВЛЕНИЕ" — текстовые поля
# ======================================================================

@router.message(AddStates.waiting_client_name)
async def add_client_name(message: Message, state: FSMContext):
    cleaned = clean_client_name(message.text)
    if not cleaned:
        await message.answer("⚠️ Название не может быть пустым. Введите название клиента:")
        return
    await state.update_data(clientName=cleaned)

    # Агент мог ошибиться в ИНН и добавлять точку, которая уже есть в базе
    # под чуть другим названием ("MUHAYO TRADE" против "MUXAYYO TRADE").
    # Прежде чем вести его по 14 шагам формы — проверяем.
    searching = await message.answer("⏳ Проверяю, нет ли такой точки в базе...")
    similar = await api.search_similar_points(cleaned)
    matches = similar.get("matches", [])

    if matches:
        await state.update_data(similar_matches=matches)
        await state.set_state(AddStates.confirm_similar)
        await searching.edit_text(
            build_similar_text(cleaned, matches),
            reply_markup=build_similar_keyboard(matches),
        )
        return

    await searching.delete()
    await state.set_state(AddStates.waiting_geo)
    await message.answer(
        f"Принято: {cleaned}\n\n"
        f"2️⃣ Отправьте геолокацию точки:\n\n"
        f"Нажмите на значок 📎 (скрепка) рядом с полем ввода → «Геопозиция» — "
        f"там можно передвинуть булавку и выбрать нужную точку на карте "
        f"(необязательно быть там физически), затем нажмите «Отправить геопозицию».",
        reply_markup=ReplyKeyboardRemove(),
    )


def build_points_text(inn: str, points: list) -> str:
    """
    Полные названия выводим текстом, а не на кнопках: у таких точек
    различие в самом конце названия (KALINA / UNILEVER / NIVEA), а Telegram
    обрезает длинные подписи кнопок — на них все варианты выглядели бы
    одинаково.
    """
    lines = [f"✅ По ИНН {inn} найдено кодов: {len(points)}", ""]
    for i, p in enumerate(points, start=1):
        lines.append(f"{i}. {p['pointName']}")
        lines.append(f"    🔢 {p['pointCode']}")
    lines += ["", "Выберите нужный номер:"]
    return "\n".join(lines)


def build_points_keyboard(points: list) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=str(i + 1), callback_data=f"pt:{i}")
               for i in range(len(points))]
    # По 4 номера в ряд, чтобы кнопки оставались крупными
    rows = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(AttachStates.choosing_point, F.data.startswith("pt:"))
async def choose_point(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[1])
    fsm_data = await state.get_data()
    points = fsm_data.get("inn_points", [])

    if idx >= len(points):
        await safe_answer(callback, "Список устарел, введите ИНН заново", show_alert=True)
        return

    point = points[idx]
    await state.update_data(
        point_code=point["pointCode"],
        point_name=point["pointName"],
        visit_days=[],
        inn_points=None,
    )
    await state.set_state(AttachStates.choosing_days)
    await callback.message.edit_text(
        f"🏪 {point['pointName']}\n"
        f"🔢 Код: {point['pointCode']}\n\n"
        f"Выберите дни визита (можно до 3):"
    )
    await callback.message.answer("👇", reply_markup=build_days_keyboard([]))
    await safe_answer(callback)


def build_similar_text(entered: str, matches: list) -> str:
    lines = [
        "⚠️ ПОХОЖАЯ ТОЧКА УЖЕ ЕСТЬ В БАЗЕ",
        "",
        f"Вы вводите: {entered}",
        "",
        "Найдено в базе:",
    ]
    for i, m in enumerate(matches, start=1):
        mark = " 🔴 пассивная" if m.get("status") == 1 else ""
        lines.append(f"{i}. {m['pointName']}\n     ИНН: {m['inn']}{mark}")
    lines += ["", "Это одна из них?"]
    return "\n".join(lines)


def build_similar_keyboard(matches: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"✅ Да, это «{m['pointName'][:28]}»",
                              callback_data=f"sim:{i}")]
        for i, m in enumerate(matches)
    ]
    rows.append([InlineKeyboardButton(text="❌ Нет, это новая точка",
                                      callback_data="sim_no")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(AddStates.confirm_similar, F.data.startswith("sim:"))
async def similar_yes(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[1])
    fsm_data = await state.get_data()
    matches = fsm_data.get("similar_matches", [])

    if idx >= len(matches):
        await safe_answer(callback, "Список устарел, начните заново", show_alert=True)
        return

    point = matches[idx]
    await callback.message.edit_reply_markup(reply_markup=None)

    if point.get("status") == 1:
        # Точка нашлась, но она пассивная — прикреплять нельзя
        agent = fsm_data.get("agent")
        await callback.message.edit_text(
            f"🔴 ЭТА ТОЧКА ПАССИВНАЯ\n\n"
            f"🏪 {point['pointName']}\n"
            f"🧾 ИНН: {point['inn']}\n\n"
            f"Обратитесь в финансовый отдел."
        )
        await state.set_data({"agent": agent})
        await ask_inn(callback.message, state)
        await safe_answer(callback)
        return

    # Переходим к прикреплению найденной точки
    agent = fsm_data.get("agent")
    await state.set_data({
        "agent": agent,
        "point_code": point["pointCode"],
        "point_name": point["pointName"],
        "visit_days": [],
    })
    await state.set_state(AttachStates.choosing_days)
    await callback.message.edit_text(
        f"✅ Прикрепляем существующую точку:\n\n"
        f"🏪 {point['pointName']}\n"
        f"🔢 Код: {point['pointCode']}\n"
        f"🧾 ИНН: {point['inn']}\n\n"
        f"Выберите дни визита (можно до 3):"
    )
    await callback.message.answer("👇", reply_markup=build_days_keyboard([]))
    await safe_answer(callback)


@router.callback_query(AddStates.confirm_similar, F.data == "sim_no")
async def similar_no(callback: CallbackQuery, state: FSMContext):
    fsm_data = await state.get_data()
    await state.update_data(similar_matches=None)
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(AddStates.waiting_geo)
    await callback.message.answer(
        f"Принято: {fsm_data.get('clientName')}\n\n"
        f"2️⃣ Отправьте геолокацию точки:\n\n"
        f"Нажмите на значок 📎 (скрепка) рядом с полем ввода → «Геопозиция» — "
        f"там можно передвинуть булавку и выбрать нужную точку на карте "
        f"(необязательно быть там физически), затем нажмите «Отправить геопозицию».",
        reply_markup=ReplyKeyboardRemove(),
    )
    await safe_answer(callback)


@router.message(AddStates.waiting_geo, F.location)
async def add_geo(message: Message, state: FSMContext):
    lat = message.location.latitude
    lon = message.location.longitude
    geo_str = f"{lat:.6f}, {lon:.6f}"
    await state.update_data(geo=geo_str)
    await state.set_state(AddStates.waiting_address)
    await message.answer(
        f"📍 Геометка принята: {geo_str}\n\n3️⃣ Введите фактический адрес доставки:",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(AddStates.waiting_geo)
async def add_geo_invalid(message: Message, state: FSMContext):
    await message.answer(
        "⚠️ Нужна именно геопозиция.\n\n"
        "Нажмите 📎 (скрепка) рядом с полем ввода → «Геопозиция», передвиньте "
        "булавку на нужную точку на карте и нажмите «Отправить геопозицию».",
    )


@router.message(AddStates.waiting_address)
async def add_address(message: Message, state: FSMContext):
    await state.update_data(address=message.text.strip())
    await state.set_state(AddStates.waiting_phone)
    await message.answer("4️⃣ Введите номер телефона (например: +998901234567):")


@router.message(AddStates.waiting_phone)
async def add_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    if not PHONE_REGEX.match(phone):
        await message.answer(
            "⚠️ Неверный формат. Номер должен быть строго в формате +998XXXXXXXXX "
            "(9 цифр после +998, без пробелов и скобок). Попробуйте ещё раз:"
        )
        return

    await state.update_data(phone=phone)
    await state.set_state(AddStates.choosing_region)
    await message.answer(
        "5️⃣ Выберите регион:",
        reply_markup=build_paginated_keyboard(data.REGIONS, "region"),
    )


# ======================================================================
# СЦЕНАРИЙ "ДОБАВЛЕНИЕ" — зависимые select'ы
# ======================================================================

@router.callback_query(AddStates.choosing_region, F.data.startswith("region:"))
async def add_region(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[1])
    region = data.REGIONS[idx]
    await state.update_data(region=region)

    oblast_list = data.OBLAST.get(region, [])
    await state.set_state(AddStates.choosing_oblast)
    await callback.message.edit_text(
        f"Регион: {region}\n\n6️⃣ Выберите область:",
        reply_markup=build_paginated_keyboard(oblast_list, "oblast"),
    )
    await safe_answer(callback)


@router.callback_query(AddStates.choosing_oblast, F.data.startswith("oblast:"))
async def add_oblast(callback: CallbackQuery, state: FSMContext):
    fsm_data = await state.get_data()
    region = fsm_data.get("region")
    idx = int(callback.data.split(":")[1])
    oblast = data.OBLAST.get(region, [])[idx]
    await state.update_data(oblast=oblast)

    okrug_list = data.OKRUG.get(oblast, [])
    await state.set_state(AddStates.choosing_okrug)
    await callback.message.edit_text(
        f"Область: {oblast}\n\n7️⃣ Выберите округ:",
        reply_markup=build_paginated_keyboard(okrug_list, "okrug"),
    )
    await safe_answer(callback)


@router.callback_query(AddStates.choosing_okrug, F.data.startswith("okrug:"))
async def add_okrug(callback: CallbackQuery, state: FSMContext):
    fsm_data = await state.get_data()
    oblast = fsm_data.get("oblast")
    idx = int(callback.data.split(":")[1])
    okrug = data.OKRUG.get(oblast, [])[idx]
    await state.update_data(okrug=okrug)

    rayon_list = data.get_rayons(okrug)
    await state.set_state(AddStates.choosing_rayon)
    await callback.message.edit_text(
        f"Округ: {okrug}\n\n8️⃣ Выберите район:",
        reply_markup=build_paginated_keyboard(rayon_list, "rayon"),
    )
    await safe_answer(callback)


@router.callback_query(AddStates.choosing_rayon, F.data.startswith("rayon:"))
async def add_rayon(callback: CallbackQuery, state: FSMContext):
    fsm_data = await state.get_data()
    idx = int(callback.data.split(":")[1])
    rayon = data.get_rayons(fsm_data.get("okrug", ""))[idx]
    await state.update_data(rayon=rayon)

    await state.set_state(AddStates.choosing_format)
    await callback.message.edit_text(
        f"Район: {rayon}\n\n9️⃣ Выберите формат:",
        reply_markup=build_paginated_keyboard(data.FORMAT, "format"),
    )
    await safe_answer(callback)


@router.callback_query(AddStates.choosing_format, F.data.startswith("format:"))
async def add_format(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[1])
    fmt = data.FORMAT[idx]
    await state.update_data(format=fmt)

    await state.set_state(AddStates.choosing_channel)
    await callback.message.edit_text(
        f"Формат: {fmt}\n\n🔟 Выберите канал:",
        reply_markup=build_paginated_keyboard(data.CHANNEL, "channel"),
    )
    await safe_answer(callback)


@router.callback_query(AddStates.choosing_channel, F.data.startswith("channel:"))
async def add_channel(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[1])
    channel = data.CHANNEL[idx]
    await state.update_data(channel=channel)

    await state.set_state(AddStates.choosing_type)
    await callback.message.edit_text(
        f"Канал: {channel}\n\n1️⃣1️⃣ Выберите тип:",
        reply_markup=build_paginated_keyboard(data.TYPE, "type"),
    )
    await safe_answer(callback)


@router.callback_query(AddStates.choosing_type, F.data.startswith("type:"))
async def add_type(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[1])
    type_value = data.TYPE[idx]
    await state.update_data(type=type_value)

    await state.set_state(AddStates.choosing_category)
    await callback.message.edit_text(
        f"Тип: {type_value}\n\n1️⃣2️⃣ Выберите категорию:",
        reply_markup=build_paginated_keyboard(data.CATEGORY, "category"),
    )
    await safe_answer(callback)


@router.callback_query(AddStates.choosing_category, F.data.startswith("category:"))
async def add_category(callback: CallbackQuery, state: FSMContext):
    fsm_data = await state.get_data()
    idx = int(callback.data.split(":")[1])
    category = data.CATEGORY[idx]
    await state.update_data(category=category)

    delivery_list = data.get_delivery_codes(fsm_data.get("oblast", ""))
    await state.set_state(AddStates.choosing_delivery)
    await callback.message.edit_text(
        f"Категория: {category}\n\n1️⃣3️⃣ Выберите код доставщика:",
        reply_markup=build_paginated_keyboard(delivery_list, "delivery"),
    )
    await safe_answer(callback)


@router.callback_query(AddStates.choosing_delivery, F.data.startswith("delivery:"))
async def add_delivery(callback: CallbackQuery, state: FSMContext):
    fsm_data = await state.get_data()
    idx = int(callback.data.split(":")[1])
    delivery_list = data.get_delivery_codes(fsm_data.get("oblast", ""))
    delivery_code = delivery_list[idx]
    await state.update_data(deliveryCode=delivery_code, visit_days=[])

    await state.set_state(AddStates.choosing_days)
    await callback.message.edit_text(
        f"Код доставщика: {delivery_code}\n\n1️⃣4️⃣ Выберите дни визита (можно до 3):",
        reply_markup=build_days_keyboard([]),
    )
    await safe_answer(callback)


# ======================================================================
# ПАГИНАЦИЯ ДЛИННЫХ СПИСКОВ
# ======================================================================

@router.callback_query(F.data == "noop")
async def noop_handler(callback: CallbackQuery):
    await safe_answer(callback)


@router.callback_query(F.data.regexp(r"^(region|oblast|okrug|rayon|format|channel|type|category|delivery)_page:\d+$"))
async def paginate_handler(callback: CallbackQuery, state: FSMContext):
    prefix, page_str = callback.data.split("_page:")
    page = int(page_str)

    fsm_data = await state.get_data()
    items = get_items_for(prefix, fsm_data)

    await callback.message.edit_reply_markup(
        reply_markup=build_paginated_keyboard(items, prefix, page=page)
    )
    await safe_answer(callback)


# ======================================================================
# ОБЩИЙ МУЛЬТИВЫБОР ДНЕЙ (используется и в "Добавление", и в "Прикрепление")
# ======================================================================

@router.callback_query(
    StateFilter(AddStates.choosing_days, AttachStates.choosing_days),
    F.data.startswith("day:"),
)
async def toggle_day(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[1])
    day = data.DAYS[idx]

    fsm_data = await state.get_data()
    selected = fsm_data.get("visit_days", [])

    if day in selected:
        selected.remove(day)
    elif len(selected) < 3:
        selected.append(day)
    else:
        await safe_answer(callback, "Можно выбрать максимум 3 дня", show_alert=True)
        return

    await state.update_data(visit_days=selected)
    await callback.message.edit_reply_markup(reply_markup=build_days_keyboard(selected))
    await safe_answer(callback)


@router.callback_query(
    StateFilter(AddStates.choosing_days, AttachStates.choosing_days),
    F.data == "day_done",
)
async def days_done(callback: CallbackQuery, state: FSMContext):
    fsm_data = await state.get_data()
    selected = fsm_data.get("visit_days", [])

    if not selected:
        await safe_answer(callback, "Выберите хотя бы 1 день визита", show_alert=True)
        return

    current_state = await state.get_state()

    if current_state == AddStates.choosing_days.state:
        await state.set_state(AddStates.waiting_comments)
        await callback.message.edit_text(f"📅 Дни визита: {', '.join(selected)}")
        await callback.message.answer(
            "1️⃣5️⃣ Введите комментарий (если его нет — отправьте символ «-»):"
        )
    else:  # AttachStates.choosing_days
        await state.set_state(AttachStates.confirm)
        await callback.message.edit_text(f"📅 Дни визита: {', '.join(selected)}")
        await send_attach_summary(callback.message, state)

    await safe_answer(callback)


# ======================================================================
# ФИНАЛ СЦЕНАРИЯ "ДОБАВЛЕНИЕ": комментарий → подтверждение → отправка
# ======================================================================

@router.message(AddStates.waiting_comments)
async def add_comments(message: Message, state: FSMContext):
    comments = message.text.strip()
    if comments == "-":
        comments = ""
    await state.update_data(comments=comments)
    await state.set_state(AddStates.confirm)
    await send_add_summary(message, state)


def build_add_summary_text(fsm_data: dict) -> str:
    return (
        "📋 Проверьте данные новой торговой точки:\n\n"
        f"🏢 Клиент: {fsm_data.get('clientName')}\n"
        f"📍 Геометка: {fsm_data.get('geo')}\n"
        f"📍 Адрес: {fsm_data.get('address')}\n"
        f"📞 Телефон: {fsm_data.get('phone')}\n"
        f"🧾 ИНН: {fsm_data.get('inn')}\n\n"
        f"🌍 Регион: {fsm_data.get('region')}\n"
        f"🏙 Область: {fsm_data.get('oblast')}\n"
        f"🏘 Округ: {fsm_data.get('okrug')}\n"
        f"📌 Район: {fsm_data.get('rayon')}\n"
        f"🏪 Формат: {fsm_data.get('format')}\n"
        f"🔀 Канал: {fsm_data.get('channel')}\n"
        f"🏷 Тип: {fsm_data.get('type')}\n"
        f"⭐ Категория: {fsm_data.get('category')}\n"
        f"🚚 Код доставщика: {fsm_data.get('deliveryCode')}\n\n"
        f"👤 Агент: {fsm_data.get('agent')}\n"
        f"📅 Дни визита: {', '.join(fsm_data.get('visit_days', []))}\n"
        f"💬 Комментарий: {fsm_data.get('comments') or '-'}"
    )


async def send_add_summary(message: Message, state: FSMContext):
    fsm_data = await state.get_data()
    text = build_add_summary_text(fsm_data)
    await message.answer(text, reply_markup=confirm_keyboard("add"))


@router.callback_query(AddStates.confirm, F.data == "add_confirm")
async def add_confirm(callback: CallbackQuery, state: FSMContext):
    await safe_answer(callback)  # снимаем "загрузку" с кнопки сразу, не дожидаясь ответа Google Sheets
    fsm_data = await state.get_data()
    await callback.message.edit_reply_markup(reply_markup=None)
    wait_msg = await callback.message.answer("⏳ Отправляю данные...")

    payload = {
        "agent": fsm_data.get("agent"),
        "clientName": fsm_data.get("clientName"),
        "geo": fsm_data.get("geo"),
        "address": fsm_data.get("address"),
        "phone": fsm_data.get("phone"),
        "inn": fsm_data.get("inn"),
        "region": fsm_data.get("region"),
        "oblast": fsm_data.get("oblast"),
        "okrug": fsm_data.get("okrug"),
        "rayon": fsm_data.get("rayon"),
        "format": fsm_data.get("format"),
        "channel": fsm_data.get("channel"),
        "type": fsm_data.get("type"),
        "category": fsm_data.get("category"),
        "deliveryCode": fsm_data.get("deliveryCode"),
        "visitDay": ", ".join(fsm_data.get("visit_days", [])),
        "comments": fsm_data.get("comments") or "",
    }

    result = await api.add_tt(payload)

    if result.get("success"):
        await wait_msg.edit_text("✅ " + result.get("message", "Точка успешно добавлена!"))
        if ADMIN_ID:
            admin_text = "📥 НОВАЯ ЗАЯВКА: ДОБАВЛЕНИЕ ТТ\n\n" + build_add_summary_text(fsm_data)
            try:
                await callback.bot.send_message(ADMIN_ID, admin_text)
            except Exception:
                logging.exception("Не удалось отправить уведомление админу")

        agent = fsm_data.get("agent")
        await state.set_data({"agent": agent})
        await ask_inn(callback.message, state)
    else:
        await wait_msg.edit_text(f"❌ Ошибка: {result.get('message')}\n\nПопробуйте отправить ещё раз.")
        await callback.message.answer("Повторить попытку?", reply_markup=confirm_keyboard("add"))


@router.callback_query(AddStates.confirm, F.data == "add_cancel")
async def add_cancel(callback: CallbackQuery, state: FSMContext):
    fsm_data = await state.get_data()
    agent = fsm_data.get("agent")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("🚫 Добавление отменено.")
    await state.set_data({"agent": agent})
    await ask_inn(callback.message, state)
    await safe_answer(callback)


# ======================================================================
# ФИНАЛ СЦЕНАРИЯ "ПРИКРЕПЛЕНИЕ": подтверждение → отправка
# ======================================================================

def build_attach_summary_text(fsm_data: dict) -> str:
    return (
        "📋 Проверьте данные прикрепления:\n\n"
        f"🏪 Точка: {fsm_data.get('point_name')}\n"
        f"🔢 Код: {fsm_data.get('point_code')}\n"
        f"👤 Агент: {fsm_data.get('agent')}\n"
        f"📅 Дни визита: {', '.join(fsm_data.get('visit_days', []))}"
    )


async def send_attach_summary(message: Message, state: FSMContext):
    fsm_data = await state.get_data()
    text = build_attach_summary_text(fsm_data)
    await message.answer(text, reply_markup=confirm_keyboard("attach"))


@router.callback_query(AttachStates.confirm, F.data == "attach_confirm")
async def attach_confirm(callback: CallbackQuery, state: FSMContext):
    await safe_answer(callback)  # снимаем "загрузку" с кнопки сразу, не дожидаясь ответа Google Sheets
    fsm_data = await state.get_data()
    await callback.message.edit_reply_markup(reply_markup=None)
    wait_msg = await callback.message.answer("⏳ Отправляю данные...")

    result = await api.attach(
        agent=fsm_data.get("agent"),
        point_code=fsm_data.get("point_code"),
        point_name=fsm_data.get("point_name"),
        visit_day=", ".join(fsm_data.get("visit_days", [])),
    )

    if result.get("success"):
        await wait_msg.edit_text("✅ " + result.get("message", "Точка успешно прикреплена!"))
        if ADMIN_ID:
            admin_text = "📎 НОВАЯ ЗАЯВКА: ПРИКРЕПЛЕНИЕ ТОЧКИ\n\n" + build_attach_summary_text(fsm_data)
            try:
                await callback.bot.send_message(ADMIN_ID, admin_text)
            except Exception:
                logging.exception("Не удалось отправить уведомление админу")

        agent = fsm_data.get("agent")
        await state.set_data({"agent": agent})
        await ask_inn(callback.message, state)
    else:
        await wait_msg.edit_text(f"❌ Ошибка: {result.get('message')}\n\nПопробуйте отправить ещё раз.")
        await callback.message.answer("Повторить попытку?", reply_markup=confirm_keyboard("attach"))


@router.callback_query(AttachStates.confirm, F.data == "attach_cancel")
async def attach_cancel(callback: CallbackQuery, state: FSMContext):
    fsm_data = await state.get_data()
    agent = fsm_data.get("agent")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("🚫 Прикрепление отменено.")
    await state.set_data({"agent": agent})
    await ask_inn(callback.message, state)
    await safe_answer(callback)


# ======================================================================
# СМЕНА ПАРОЛЯ: шаги сценария
# ======================================================================

@router.message(ChangePasswordStates.waiting_current, F.text)
async def change_password_current(message: Message, state: FSMContext):
    current_password = message.text.strip()
    fsm_data = await state.get_data()
    agent = fsm_data.get("agent", "")

    await delete_message_safe(message)

    checking_msg = await message.answer("⏳ Проверяю текущий пароль...")
    result = await api.login(agent.lower(), current_password)

    if not result.get("success"):
        await checking_msg.edit_text(
            "❌ Текущий пароль неверный.\n\n"
            "Введите его ещё раз или наберите /cancel:"
        )
        return

    await state.update_data(current_password=current_password)
    await state.set_state(ChangePasswordStates.waiting_new)
    await checking_msg.edit_text(
        f"✅ Пароль подтверждён.\n\n"
        f"Введите НОВЫЙ пароль "
        f"(от {MIN_PASSWORD_LENGTH} до {MAX_PASSWORD_LENGTH} символов, без пробелов):"
    )


@router.message(ChangePasswordStates.waiting_new, F.text)
async def change_password_new(message: Message, state: FSMContext):
    new_password = message.text.strip()
    fsm_data = await state.get_data()

    await delete_message_safe(message)

    error = validate_new_password(new_password, fsm_data.get("current_password", ""))
    if error:
        await message.answer(f"⚠️ {error}\n\nВведите новый пароль ещё раз:")
        return

    await state.update_data(new_password=new_password)
    await state.set_state(ChangePasswordStates.waiting_repeat)
    await message.answer("Повторите новый пароль ещё раз для подтверждения:")


@router.message(ChangePasswordStates.waiting_repeat, F.text)
async def change_password_repeat(message: Message, state: FSMContext):
    repeat = message.text.strip()
    fsm_data = await state.get_data()

    await delete_message_safe(message)

    if repeat != fsm_data.get("new_password"):
        await state.set_state(ChangePasswordStates.waiting_new)
        await message.answer(
            "❌ Пароли не совпадают.\n\nВведите новый пароль заново:"
        )
        return

    agent = fsm_data.get("agent", "")
    wait_msg = await message.answer("⏳ Сохраняю новый пароль...")

    result = await api.change_password(
        login_value=agent.lower(),
        current_password=fsm_data.get("current_password", ""),
        new_password=fsm_data.get("new_password", ""),
    )

    if result.get("success"):
        await wait_msg.edit_text(
            "✅ Пароль изменён.\n\n"
            "В следующий раз входите с новым паролем — старый больше не работает."
        )
        if ADMIN_ID:
            try:
                # Сам пароль админу НЕ отправляем — только факт смены
                await message.bot.send_message(
                    ADMIN_ID, f"🔑 Агент {agent} сменил пароль."
                )
            except Exception:
                logging.exception("Не удалось отправить уведомление админу")
    else:
        await wait_msg.edit_text(
            f"❌ Не удалось сменить пароль: {result.get('message')}\n\n"
            f"Пароль остался прежним, попробуйте позже — /change_password"
        )

    # В любом случае чистим временные пароли из состояния и возвращаемся к ИНН
    await state.set_data({"agent": agent})
    await ask_inn(message, state)


@router.message(
    StateFilter(
        ChangePasswordStates.waiting_current,
        ChangePasswordStates.waiting_new,
        ChangePasswordStates.waiting_repeat,
    )
)
async def change_password_invalid(message: Message, state: FSMContext):
    """Пользователь прислал не текст (стикер, фото, голосовое) вместо пароля."""
    await message.answer("⚠️ Пароль нужно ввести текстом. Попробуйте ещё раз или /cancel:")


def validate_new_password(password: str, current_password: str) -> str | None:
    """Возвращает текст ошибки или None, если пароль подходит."""
    if password == current_password:
        # Эта проверка идёт первой: если старый пароль короткий (например "a"),
        # то при попытке оставить его же сообщение про длину только запутает.
        return "Новый пароль совпадает со старым — придумайте другой."
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Слишком короткий пароль — минимум {MIN_PASSWORD_LENGTH} символа."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Слишком длинный пароль — максимум {MAX_PASSWORD_LENGTH} символов."
    if " " in password:
        return "Пароль не должен содержать пробелов."
    if password.startswith("/"):
        return "Пароль не может начинаться со знака «/» — так его примут за команду."
    return None


# ======================================================================
# MAIN
# ======================================================================

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Команды в меню Telegram (кнопка "/" рядом с полем ввода)
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать заново / вход"),
        BotCommand(command="cancel", description="Отменить текущий сценарий"),
        BotCommand(command="back", description="Вернуться на шаг назад"),
        BotCommand(command="change_password", description="Сменить пароль"),
    ])

    await bot.delete_webhook(drop_pending_updates=True)

    # Веб-сервис для сайта (index.html) поднимается в том же процессе:
    # так бот и сайт делят один пул соединений с базой, и на Railway
    # достаточно одного сервиса вместо двух.
    web_runner = await start_web_server()

    try:
        await dp.start_polling(bot)
    finally:
        await web_runner.cleanup()
        await api.close_pool()


if __name__ == "__main__":
    asyncio.run(main())