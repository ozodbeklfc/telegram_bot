from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from data import DAYS

PAGE_SIZE = 10


def build_paginated_keyboard(
    items: list[str], prefix: str, page: int = 0, columns: int = 1
) -> InlineKeyboardMarkup:
    """
    Клавиатура для длинных списков с пагинацией.
    Индекс кнопки в callback_data — ГЛОБАЛЬНЫЙ (по всему списку, а не по странице),
    поэтому обработчик выбора значения не меняется независимо от страницы.
    Если список умещается на одну страницу — кнопки навигации не показываются.
    """
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_items = items[start:end]

    buttons = [
        InlineKeyboardButton(text=item, callback_data=f"{prefix}:{start + i}")
        for i, item in enumerate(page_items)
    ]
    rows = [buttons[i:i + columns] for i in range(0, len(buttons), columns)]

    total_pages = max(1, (len(items) - 1) // PAGE_SIZE + 1)
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"{prefix}_page:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
        if end < len(items):
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"{prefix}_page:{page + 1}"))
        rows.append(nav)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_days_keyboard(selected: list[str], taken: list[str] = None) -> InlineKeyboardMarkup:
    """
    Мультивыбор дней недели. Максимум 3 дня на точку.

    selected — выбранные сейчас дни (помечаются галочкой)
    taken    — дни, уже занятые этим же агентом на этой точке по прошлым
               прикреплениям. Помечаются замком и повторно не выбираются:
               иначе агент занял бы один день дважды и упёрся в лимит зря.
    """
    taken = taken or []
    rows = []
    for idx, day in enumerate(DAYS):
        if day in taken:
            mark = "🔒 "
        elif day in selected:
            mark = "✅ "
        else:
            mark = ""
        rows.append([InlineKeyboardButton(text=f"{mark}{day}", callback_data=f"day:{idx}")])

    done_text = f"➡️ Готово ({len(selected)} выбрано)" if selected else "➡️ Готово"
    rows.append([InlineKeyboardButton(text=done_text, callback_data="day_done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Отправить", callback_data=f"{prefix}_confirm"),
        InlineKeyboardButton(text="❌ Отменить", callback_data=f"{prefix}_cancel"),
    ]])
