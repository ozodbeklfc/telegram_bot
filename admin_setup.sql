-- ======================================================================
-- Админ-панель: роли и бренды пользователей
--
-- Выполнять один раз в Railway -> Postgres -> Query.
-- Скрипт безопасно запускать повторно.
-- ======================================================================

-- ---------- 1. Роль и бренд ----------
-- role: 'agent' — обычный агент, 'admin' — видит агентов своего бренда
ALTER TABLE users ADD COLUMN IF NOT EXISTS role  TEXT NOT NULL DEFAULT 'agent';
ALTER TABLE users ADD COLUMN IF NOT EXISTS brand TEXT;

-- Бренд — первые два символа логина: UL0112 и ULTP0101 оба относятся к UL
UPDATE users
   SET brand = upper(left(login, 2))
 WHERE brand IS NULL;

CREATE INDEX IF NOT EXISTS idx_users_brand ON users (brand);

-- ---------- 2. Создать админов ----------
-- Пароли задайте свои. Логин может быть любым — бренд берётся из колонки
-- brand, а не из логина, поэтому его указываем явно.
INSERT INTO users (login, password, role, brand) VALUES
    ('admin_ul', 'СМЕНИТЕ_ПАРОЛЬ', 'admin', 'UL'),
    ('admin_or', 'СМЕНИТЕ_ПАРОЛЬ', 'admin', 'OR')
ON CONFLICT (login) DO UPDATE
    SET role = EXCLUDED.role,
        brand = EXCLUDED.brand;

-- ---------- 3. Проверка ----------
--   SELECT login, role, brand FROM users WHERE role = 'admin';
--   SELECT brand, count(*) FROM users GROUP BY brand ORDER BY 2 DESC;
