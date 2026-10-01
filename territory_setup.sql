-- ======================================================================
-- Территория в учётной записи
--
-- Нужна, только если хотите, чтобы колонка «Территория» из SUPERVISORS.xlsx
-- сохранялась в базе. Без неё загрузка структуры всё равно пройдёт —
-- скрипт просто пропустит территорию и скажет об этом.
--
-- Выполнять в Railway -> Postgres -> Query.
-- ======================================================================

ALTER TABLE users ADD COLUMN IF NOT EXISTS territory TEXT;

CREATE INDEX IF NOT EXISTS idx_users_territory ON users (territory);

-- Проверка после загрузки:
--   SELECT territory, count(*) FROM users GROUP BY 1 ORDER BY 1;
