-- ======================================================================
-- Схема базы данных для Telegram-бота "Управление ТТ"
-- Повторяет структуру твоих 4 листов Google Таблицы:
--   Пользователи → users
--   Клиентская база → client_base
--   Прикрепление → attachments
--   Добавление → add_requests
-- ======================================================================

CREATE TABLE IF NOT EXISTS users (
    login    TEXT PRIMARY KEY,
    password TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS client_base (
    id         SERIAL PRIMARY KEY,
    point_code TEXT,
    point_name TEXT,
    inn        TEXT NOT NULL UNIQUE
);

-- Индекс для мгновенного поиска по ИНН (то, ради чего всё затевалось)
CREATE INDEX IF NOT EXISTS idx_client_base_inn ON client_base (inn);

CREATE TABLE IF NOT EXISTS attachments (
    id          SERIAL PRIMARY KEY,
    point_code  TEXT,
    point_name  TEXT,
    agent_brand TEXT,
    agent       TEXT,
    visit_day   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS add_requests (
    id            SERIAL PRIMARY KEY,
    client_name   TEXT,
    geo           TEXT,
    address       TEXT,
    phone         TEXT,
    inn           TEXT,
    region        TEXT,
    oblast        TEXT,
    okrug         TEXT,
    rayon         TEXT,
    format        TEXT,
    channel       TEXT,
    type          TEXT,
    category      TEXT,
    delivery_code TEXT,
    agent         TEXT,
    visit_day     TEXT,
    comments      TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
