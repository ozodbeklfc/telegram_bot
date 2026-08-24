-- ======================================================================
-- Админ-панель: роли и бренды
--
-- Выполнять в Railway -> Postgres -> Query.
-- ВАЖНО: запускайте блоки ПО ОДНОМУ. Railway показывает результат только
-- последнего запроса, и при нескольких сразу непонятно, что отработало.
-- ======================================================================


-- ---------- ШАГ 1. Колонки ----------
ALTER TABLE users ADD COLUMN IF NOT EXISTS role  TEXT NOT NULL DEFAULT 'agent';
ALTER TABLE users ADD COLUMN IF NOT EXISTS brand TEXT;

UPDATE users
   SET brand = upper(left(login, 2))
 WHERE brand IS NULL;

CREATE INDEX IF NOT EXISTS idx_users_brand ON users (brand);


-- ---------- ШАГ 2а. ОБЩИЙ АДМИН ----------
-- Один логин на всех: после входа сам выбирает бренд из списка.
-- brand = NULL как раз и означает «доступ ко всем брендам».
INSERT INTO users (login, password, role, brand)
VALUES ('admin', '123', 'admin', NULL)
ON CONFLICT (login) DO UPDATE
    SET role = 'admin', brand = NULL;


-- ---------- ШАГ 2б. Админы отдельных брендов (по желанию) ----------
-- Нужны, только если хотите, чтобы человек видел ровно один бренд
-- и не мог переключиться. Если хватает общего логина — этот шаг пропустите.
-- Бренды берутся из логинов агентов, перечислять руками не нужно.
-- Логин админа: admin_ul, admin_or, admin_fm и т.д.
INSERT INTO users (login, password, role, brand)
SELECT 'admin_' || lower(b.brand), '123', 'admin', b.brand
  FROM (SELECT DISTINCT COALESCE(brand, upper(left(login, 2))) AS brand
          FROM users
         WHERE COALESCE(role, 'agent') = 'agent') b
ON CONFLICT (login) DO UPDATE
    SET role  = EXCLUDED.role,
        brand = EXCLUDED.brand;


-- ---------- ШАГ 3. Проверка ----------
SELECT login, brand FROM users WHERE role = 'admin' ORDER BY login;


-- ---------- ШАГ 4. Сменить пароли ----------
-- У всех админов пароль '123' — замените сразу после первого входа.
-- UPDATE users SET password = 'ваш_пароль' WHERE login = 'admin_ul';
-- UPDATE users SET password = 'ваш_пароль' WHERE login = 'admin_or';


-- ---------- Полезное ----------
-- Сколько агентов в каждом бренде:
--   SELECT brand, count(*) FROM users
--    WHERE role = 'agent' GROUP BY brand ORDER BY 2 DESC;
--
-- Сделать админом существующего пользователя:
--   UPDATE users SET role = 'admin', brand = 'UL' WHERE login = 'ozodbek';
