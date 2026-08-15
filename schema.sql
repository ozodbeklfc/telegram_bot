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
    -- Только цифры: пометки вроде '306955509+' очищаются при загрузке
    inn        TEXT NOT NULL UNIQUE,
    -- Исходное значение из выгрузки, со всеми пометками
    inn_raw    TEXT,
    -- 0 = активная точка, 1 = пассивная (агент работать с ней не может)
    status     SMALLINT NOT NULL DEFAULT 0
);

-- UNIQUE на inn уже создаёт индекс, отдельный не нужен

-- Поиск похожих названий: ловит опечатки вроде MUHAYO / MUXAYYO
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_client_base_name_trgm
    ON client_base USING gin (point_name gin_trgm_ops);

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
