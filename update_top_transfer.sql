-- ======================================================================
-- Обновление: ТОП-точки, новые лимиты и передача точки
--
-- Что меняется в правилах:
--   • 36 визитов в день вместо 30 (значит 180 визитов в неделю);
--   • лимит 150 точек теперь считается по УНИКАЛЬНЫМ ИНН, а не по кодам
--     контрагента: три кода одной точки — одно место, потому что агент
--     приезжает туда один раз;
--   • обычная точка закрепляется РОВНО на один день. Ритейл обходят
--     раз в неделю, и лишние дни приводили к заказам вне маршрута;
--   • три дня разрешены только ТОП-точкам.
--
-- Выполнять в Railway -> Postgres -> Query. Блоки запускать ПО ОДНОМУ.
-- ======================================================================


-- ---------- ШАГ 1. Признак ТОП-точки ----------
-- Отдельная колонка, а не значение в type: сетевая и ТОП — разные вещи,
-- и одна точка бывает и той, и другой одновременно (сетевой супермаркет
-- с большим оборотом). Если сложить их в одну колонку, отметив ТОП,
-- мы бы потеряли «сетевая» и на точке снова появилась бы проблема
-- двух агентов одного бренда.
ALTER TABLE client_base ADD COLUMN IF NOT EXISTS is_top BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_client_base_is_top ON client_base (is_top)
    WHERE is_top;


-- ---------- ШАГ 2. Индекс под счёт по ИНН ----------
-- Лимит точек теперь считается как количество разных ИНН у агента,
-- а это соединение attachments с client_base на каждый ввод ИНН.
CREATE INDEX IF NOT EXISTS idx_client_base_code_inn ON client_base (point_code, inn);


-- ---------- ШАГ 3. Журнал передач ----------
-- Кто, когда и кому передал точку. Нужен не для отчётности, а для
-- разбора: после массовой разгрузки агенты будут спрашивать, куда делась
-- их точка, и ответ должен быть в базе, а не в памяти супервайзера.
CREATE TABLE IF NOT EXISTS transfer_log (
    id          SERIAL PRIMARY KEY,
    point_code  TEXT NOT NULL,
    point_name  TEXT,
    from_agent  TEXT NOT NULL,
    to_agent    TEXT NOT NULL,
    days        TEXT,
    by_login    TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_transfer_log_point ON transfer_log (point_code);
CREATE INDEX IF NOT EXISTS idx_transfer_log_from  ON transfer_log (upper(from_agent));
CREATE INDEX IF NOT EXISTS idx_transfer_log_when  ON transfer_log (created_at DESC);


-- ---------- ШАГ 4. Проверка ----------
SELECT
    (SELECT count(*) FROM client_base WHERE is_top)      AS top_tochek,
    (SELECT count(*) FROM client_base WHERE type='chain') AS setevyh,
    (SELECT count(*) FROM transfer_log)                   AS peredach;


-- ---------- ШАГ 5 (необязательный). Кто не влезает в новые правила ----------
-- Правила действуют на НОВЫЕ прикрепления. Эти запросы показывают,
-- что придётся разбирать супервайзеру.
--
-- Агенты сверх 150 точек (счёт по уникальным ИНН):
--   SELECT upper(a.agent) AS agent,
--          count(DISTINCT COALESCE(c.inn, a.point_code)) AS tochek
--     FROM attachments a
--     LEFT JOIN client_base c ON c.point_code = a.point_code
--    GROUP BY 1 HAVING count(DISTINCT COALESCE(c.inn, a.point_code)) > 150
--    ORDER BY 2 DESC;
--
-- Обычные точки, где стоит больше одного дня (главная работа по разгрузке):
--   SELECT upper(a.agent) AS agent, count(*) AS tochek_s_lishnimi_dnyami
--     FROM (SELECT upper(agent) AS agent, point_code, count(DISTINCT visit_day) AS d
--             FROM attachments GROUP BY 1, 2) a
--     LEFT JOIN client_base c ON c.point_code = a.point_code
--    WHERE a.d > 1 AND NOT COALESCE(c.is_top, false)
--    GROUP BY 1 ORDER BY 2 DESC;
--
-- Перегруженные дни (больше 36 визитов):
--   SELECT upper(a.agent) AS agent, a.visit_day,
--          count(DISTINCT COALESCE(c.inn, a.point_code)) AS vizitov
--     FROM attachments a
--     LEFT JOIN client_base c ON c.point_code = a.point_code
--    GROUP BY 1, 2
--   HAVING count(DISTINCT COALESCE(c.inn, a.point_code)) > 36
--    ORDER BY 3 DESC;
