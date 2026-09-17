-- ======================================================================
-- Обновление: лимиты, роль оператора, заявки и архив
--
-- Выполнять в Railway -> Postgres -> Query. Блоки запускать ПО ОДНОМУ:
-- Railway показывает результат только последнего запроса, и при нескольких
-- сразу непонятно, что отработало.
-- ======================================================================


-- ---------- ШАГ 1. Telegram-идентификатор агента ----------
-- Нужен, чтобы бот мог написать агенту сам, без входящего сообщения:
-- когда оператор жмёт «Готово», уведомление уходит именно этому человеку.
-- Заполняется автоматически при каждом входе агента в бота.
ALTER TABLE users ADD COLUMN IF NOT EXISTS tg_id BIGINT;

CREATE INDEX IF NOT EXISTS idx_users_tg_id ON users (tg_id);


-- ---------- ШАГ 2. Очередь заявок оператора ----------
-- Заявки не подменяют собой attachments и add_requests: те таблицы
-- остаются рабочими данными, а здесь лежит очередь обработки —
-- что оператор уже провёл в системе, а что ещё нет.
--
-- payload хранит заявку целиком (JSONB): состав полей у добавления ТТ
-- со временем меняется, и отдельные колонки под каждое поле пришлось бы
-- добавлять заново при каждой правке формы.
CREATE TABLE IF NOT EXISTS operator_requests (
    id          SERIAL PRIMARY KEY,
    -- 'attach' — прикрепление точки, 'add' — добавление новой ТТ
    kind        TEXT NOT NULL CHECK (kind IN ('attach', 'add')),
    agent       TEXT NOT NULL,
    agent_brand TEXT,
    -- Копия tg_id на момент заявки: если агент потом сменит телефон,
    -- уведомление всё равно уйдёт по актуальному значению из users,
    -- а это останется как след
    tg_id       BIGINT,
    point_code  TEXT,
    point_name  TEXT,
    inn         TEXT,
    visit_day   TEXT,
    -- 'bot' или 'site' — видно, откуда пришла заявка
    source      TEXT,
    payload     JSONB,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'done')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    done_at     TIMESTAMPTZ,
    done_by     TEXT
);

-- Лист оператора: сначала ожидающие, новые сверху
CREATE INDEX IF NOT EXISTS idx_oreq_status
    ON operator_requests (status, created_at DESC);

-- «Мои заявки» в приложении агента
CREATE INDEX IF NOT EXISTS idx_oreq_agent
    ON operator_requests (upper(agent), created_at DESC);

-- Архив по дню
CREATE INDEX IF NOT EXISTS idx_oreq_done_at
    ON operator_requests (done_at DESC);


-- ---------- ШАГ 3. Учётная запись оператора ----------
-- Роль 'operator': видит только лист заявок и архив. Ни агентов,
-- ни точек, ни паролей других людей.
-- ПАРОЛЬ СМЕНИТЬ СРАЗУ ПОСЛЕ ПЕРВОГО ВХОДА.
INSERT INTO users (login, password, role, brand)
VALUES ('operator', '123', 'operator', NULL)
ON CONFLICT (login) DO UPDATE
    SET role = 'operator', brand = NULL;


-- ---------- ШАГ 4. Индексы под лимиты ----------
-- Лимит 150 точек считается по числу разных точек агента,
-- лимит 30 визитов — по числу точек агента в один день недели.
CREATE INDEX IF NOT EXISTS idx_attachments_agent_day
    ON attachments (upper(agent), visit_day);


-- ---------- ШАГ 5. Проверка ----------
SELECT
    (SELECT count(*) FROM users WHERE role = 'operator')                     AS operatorov,
    (SELECT count(*) FROM operator_requests WHERE status = 'pending')        AS zayavok_v_ojidanii,
    (SELECT count(*) FROM users WHERE tg_id IS NOT NULL)                     AS agentov_s_telegram;


-- ---------- ШАГ 6 (необязательный). Кто уже за лимитом ----------
-- Правила применяются только к НОВЫМ прикреплениям: старые перекосы
-- разбирает супервайзер. Этот запрос показывает, где они есть.
--
-- Агенты с больше чем 150 точками:
--   SELECT upper(agent) AS agent, count(DISTINCT point_code) AS tochek
--     FROM attachments GROUP BY 1 HAVING count(DISTINCT point_code) > 150
--     ORDER BY 2 DESC;
--
-- Перегруженные дни (больше 30 визитов):
--   SELECT upper(agent) AS agent, visit_day, count(DISTINCT point_code) AS vizitov
--     FROM attachments GROUP BY 1, 2 HAVING count(DISTINCT point_code) > 30
--     ORDER BY 3 DESC;
