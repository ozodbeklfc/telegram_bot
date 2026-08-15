-- ======================================================================
-- Обновление структуры client_base под новый формат выгрузки
--   Код Контрагента ; Название ; ИНН ; Статус
--
-- Что меняется и почему:
--   1. Уникальность переносится с ИНН на код контрагента.
--      В выгрузке у одной точки несколько строк с одним ИНН, но разными
--      кодами (KALINA, UNILEVER, NIVEA...). При UNIQUE(inn) из девяти
--      таких строк в базе осталась бы одна.
--   2. ИНН становится необязательным: у служебных строк (DILERLER,
--      PERSONAL ZAKAZI) его нет, и раньше они просто отбрасывались.
--   3. Колонка inn_raw убирается — ИНН чистится до загрузки.
--
-- Выполнять в Railway -> Postgres -> Query.
-- ======================================================================

-- ВАЖНО: таблица очищается, иначе шаг с UNIQUE упадёт на старых дублях.
-- Данные всё равно будут перезалиты миграцией. Заявки в attachments
-- и add_requests не затрагиваются.
TRUNCATE client_base RESTART IDENTITY;

-- ---------- 1. Убираем inn_raw ----------
ALTER TABLE client_base DROP COLUMN IF EXISTS inn_raw;

-- ---------- 2. ИНН: необязательный и больше не уникальный ----------
ALTER TABLE client_base ALTER COLUMN inn DROP NOT NULL;
ALTER TABLE client_base DROP CONSTRAINT IF EXISTS client_base_inn_key;

-- Поиск по ИНН остаётся быстрым за счёт обычного индекса
CREATE INDEX IF NOT EXISTS idx_client_base_inn ON client_base (inn);

-- ---------- 3. Уникальность — по коду контрагента ----------
ALTER TABLE client_base DROP CONSTRAINT IF EXISTS client_base_point_code_key;
ALTER TABLE client_base ADD CONSTRAINT client_base_point_code_key UNIQUE (point_code);

-- ---------- 4. Статус ----------
-- 0 = активная, 1 = пассивная. Значение берётся из четвёртой колонки файла.
ALTER TABLE client_base ADD COLUMN IF NOT EXISTS status SMALLINT NOT NULL DEFAULT 0;

-- ---------- 5. Поиск похожих названий ----------
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_client_base_name_trgm
    ON client_base USING gin (point_name gin_trgm_ops);

-- ---------- Проверка после загрузки ----------
--   SELECT status, count(*) FROM client_base GROUP BY status;
--   SELECT count(*) FROM client_base WHERE inn IS NULL;
