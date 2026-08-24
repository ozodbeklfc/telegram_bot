-- ======================================================================
-- Большое обновление: супервайзеры, тип точки, разбор проблем
-- Выполнять в Railway -> Postgres -> Query. Блоки запускать по одному.
-- ======================================================================


-- ---------- ШАГ 1. Супервайзеры ----------
-- role: 'agent' | 'supervisor' | 'admin'
-- supervisor: код супервайзера, за которым закреплён агент (ULS0101)
ALTER TABLE users ADD COLUMN IF NOT EXISTS role       TEXT NOT NULL DEFAULT 'agent';
ALTER TABLE users ADD COLUMN IF NOT EXISTS brand      TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS supervisor TEXT;

UPDATE users SET brand = upper(left(login, 2)) WHERE brand IS NULL;

CREATE INDEX IF NOT EXISTS idx_users_brand      ON users (brand);
CREATE INDEX IF NOT EXISTS idx_users_supervisor ON users (supervisor);


-- ---------- ШАГ 2. Тип точки ----------
-- 'def'   — обычная точка: агент своего бренда на ней может быть только один
-- 'chain' — сетевая: на ней разрешено несколько агентов одного бренда
ALTER TABLE client_base ADD COLUMN IF NOT EXISTS type TEXT NOT NULL DEFAULT 'def';

ALTER TABLE client_base DROP CONSTRAINT IF EXISTS client_base_type_check;
ALTER TABLE client_base ADD CONSTRAINT client_base_type_check
    CHECK (type IN ('def', 'chain'));

CREATE INDEX IF NOT EXISTS idx_client_base_type ON client_base (type);


-- ---------- ШАГ 3. Индексы под разбор проблем ----------
-- Проблемы ищутся группировкой по точке и агенту
CREATE INDEX IF NOT EXISTS idx_attachments_point_agent
    ON attachments (point_code, agent);


-- ---------- ШАГ 4. Проверка ----------
SELECT
    (SELECT count(*) FROM users WHERE role = 'supervisor')      AS supervayzerov,
    (SELECT count(*) FROM users WHERE supervisor IS NOT NULL)   AS agentov_s_sv,
    (SELECT count(*) FROM client_base WHERE type = 'chain')     AS setevyh_tochek;


-- ---------- Полезные запросы ----------
-- Точки, где у агента больше трёх дней:
--   SELECT point_code, agent, count(*) FROM attachments
--    GROUP BY 1,2 HAVING count(*) > 3 ORDER BY 3 DESC;
--
-- Точки, где несколько агентов одного бренда (кроме сетевых):
--   SELECT a.point_code, a.agent_brand, string_agg(DISTINCT a.agent, ', ')
--     FROM attachments a
--     LEFT JOIN client_base c ON c.point_code = a.point_code
--    WHERE COALESCE(c.type, 'def') <> 'chain'
--    GROUP BY 1,2 HAVING count(DISTINCT a.agent) > 1;
