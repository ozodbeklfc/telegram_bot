-- =====================================================================
--  ДУБЛИ В ПРИКРЕПЛЕНИЯХ — ТОЛЬКО ПОСМОТРЕТЬ
-- =====================================================================
--
--  ЧТО СЧИТАЕМ ДУБЛЕМ
--  Две и более строки с одинаковой тройкой: код контрагента + агент +
--  день визита. Такая строка не означает второй визит: агент приезжает
--  в точку в этот день один раз. Разные id и разное время создания
--  дублем быть не мешают — это служебные поля.
--
--  ОТКУДА ОНИ БЕРУТСЯ
--  В таблице нет ограничения на уникальность: есть только ключ по id,
--  а он у каждой строки свой. Поэтому повторная загрузка выгрузки,
--  двойное нажатие в боте или перезапуск миграции кладут строку рядом,
--  а не поверх.
--
--  ЧЕМ МЕШАЮТ
--  Дни визита на точке считались по числу СТРОК, поэтому точка с
--  дублем выглядела как «2 дня вместо 1» — то есть нарушением,
--  которого нет. В панели это видно как расхождение: написано «2 дня»,
--  а в списке дней стоит один. В новом api.py счёт уже по разным дням,
--  но сами лишние строки всё равно надо убрать.
--
--  Этот файл НИЧЕГО НЕ МЕНЯЕТ. Запускайте спокойно.
--  Запросы выполняйте по одному, сверху вниз.
--
-- =====================================================================


-- ---------------------------------------------------------------------
--  1. СКОЛЬКО ИХ — с этого начните
-- ---------------------------------------------------------------------
--  lishnih_strok — столько строк удалится. Это не число точек: у одной
--  точки может быть несколько лишних строк.

SELECT
    (SELECT count(*) FROM attachments)                   AS vsego_strok,
    COALESCE(sum(n - 1), 0)                              AS lishnih_strok,
    count(*)                                             AS grupp_s_dublyami,
    count(DISTINCT point_code)                           AS zatronuto_tochek
  FROM (
        SELECT point_code, upper(agent) AS agent, visit_day, count(*) AS n
          FROM attachments
         WHERE agent IS NOT NULL AND btrim(agent) <> ''
           AND visit_day IS NOT NULL AND btrim(visit_day) <> ''
         GROUP BY point_code, upper(agent), visit_day
        HAVING count(*) > 1
       ) d;


-- ---------------------------------------------------------------------
--  2. У КОГО ИМЕННО
-- ---------------------------------------------------------------------
--  Если дубли собрались у одного-двух агентов — скорее всего, причина
--  в разовой операции по ним. Если ровным слоем по всем — причина
--  общая: повторная загрузка выгрузки.

SELECT upper(agent)              AS agent,
       count(*)                  AS grupp_s_dublyami,
       sum(n - 1)                AS lishnih_strok,
       min(point_code)           AS primer_tochki
  FROM (
        SELECT point_code, agent, visit_day, count(*) AS n
          FROM attachments
         WHERE agent IS NOT NULL AND btrim(agent) <> ''
           AND visit_day IS NOT NULL AND btrim(visit_day) <> ''
         GROUP BY point_code, agent, visit_day
        HAVING count(*) > 1
       ) d
 GROUP BY upper(agent)
 ORDER BY lishnih_strok DESC
 LIMIT 50;


-- ---------------------------------------------------------------------
--  3. ПОСМОТРЕТЬ СТРОКИ ЦЕЛИКОМ
-- ---------------------------------------------------------------------
--  Убедиться своими глазами, что это и правда одно и то же, а не две
--  разные записи. Остаётся строка с наименьшим id — самая ранняя.

SELECT a.id, a.point_code, a.point_name, a.agent, a.visit_day,
       a.created_at AT TIME ZONE 'Asia/Tashkent' AS sozdana,
       CASE WHEN a.id = min(a.id) OVER (
                       PARTITION BY a.point_code, upper(a.agent), a.visit_day)
            THEN 'останется' ELSE 'удалится' END AS reshenie
  FROM attachments a
  JOIN (
        SELECT point_code, upper(agent) AS agent, visit_day
          FROM attachments
         WHERE agent IS NOT NULL AND btrim(agent) <> ''
           AND visit_day IS NOT NULL AND btrim(visit_day) <> ''
         GROUP BY point_code, upper(agent), visit_day
        HAVING count(*) > 1
       ) d
    ON d.point_code = a.point_code
   AND d.agent = upper(a.agent)
   AND d.visit_day = a.visit_day
 ORDER BY a.point_code, upper(a.agent), a.visit_day, a.id
 LIMIT 100;


-- ---------------------------------------------------------------------
--  4. ОТДЕЛЬНЫЙ СЛУЧАЙ: РАЗНЫЙ РЕГИСТР ЛОГИНА
-- ---------------------------------------------------------------------
--  'or0111' и 'OR0111' — один и тот же агент, но строки разные, и на
--  глаз такой дубль не виден. Система везде приводит логин к верхнему
--  регистру, поэтому для неё это одна запись.
--
--  Если здесь пусто — беспокоиться не о чем.

SELECT point_code, visit_day,
       string_agg(DISTINCT agent, ' / ') AS kak_zapisan,
       count(*)                          AS strok
  FROM attachments
 WHERE agent IS NOT NULL AND btrim(agent) <> ''
   AND visit_day IS NOT NULL AND btrim(visit_day) <> ''
 GROUP BY point_code, upper(agent), visit_day
HAVING count(DISTINCT agent) > 1
 LIMIT 50;


-- ---------------------------------------------------------------------
--  5. ЧТО ИЗМЕНИТСЯ В ПАНЕЛИ ПОСЛЕ ЧИСТКИ
-- ---------------------------------------------------------------------
--  Точки, которые числятся нарушением ТОЛЬКО из-за дублей: по строкам
--  дней больше нормы, а по разным дням — ровно норма. После чистки
--  они перестанут быть проблемными.

SELECT upper(a.agent)                  AS agent,
       a.point_code,
       max(a.point_name)               AS tochka,
       count(*)                        AS strok,
       count(DISTINCT a.visit_day)     AS realno_dnei,
       CASE WHEN COALESCE(c.is_top, false) THEN 3 ELSE 1 END AS norma
  FROM attachments a
  LEFT JOIN client_base c ON c.point_code = a.point_code
 WHERE a.agent IS NOT NULL AND btrim(a.agent) <> ''
 GROUP BY upper(a.agent), a.point_code, COALESCE(c.is_top, false)
HAVING count(*) > CASE WHEN COALESCE(c.is_top, false) THEN 3 ELSE 1 END
   AND count(DISTINCT a.visit_day)
       <= CASE WHEN COALESCE(c.is_top, false) THEN 3 ELSE 1 END
 ORDER BY strok DESC
 LIMIT 100;
