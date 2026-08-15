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
    -- Уникален именно код контрагента: у одной точки бывает несколько
    -- кодов с общим ИНН (KALINA, UNILEVER, NIVEA — разные категории)
    point_code TEXT NOT NULL UNIQUE,
    point_name TEXT,
    -- Только цифры, символы вычищаются при загрузке.
    -- NULL — у служебных строк (DILERLER, PERSONAL ZAKAZI)
    inn        TEXT,
    -- 0 = активная точка, 1 = пассивная (агент работать с ней не может)
    status     SMALLINT NOT NULL DEFAULT 0
);

-- Поиск по ИНН (то, ради чего всё затевалось)
CREATE INDEX IF NOT EXISTS idx_client_base_inn ON client_base (inn);

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

-- Проверка правил прикрепления идёт при каждом вводе ИНН — нужен индекс
CREATE INDEX IF NOT EXISTS idx_attachments_point ON attachments (point_code);
CREATE INDEX IF NOT EXISTS idx_attachments_agent ON attachments (agent);
