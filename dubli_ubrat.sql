-- =====================================================================
--  УДАЛЕНИЕ ДУБЛЕЙ И ЗАЩИТА ОТ НОВЫХ
-- =====================================================================
--
--  ЭТОТ ФАЙЛ МЕНЯЕТ ДАННЫЕ. Сначала запустите dubli_otchet.sql и
--  посмотрите, сколько строк удалится.
--
--  ЧТО ДЕЛАЕТ
--  1) складывает копию таблицы в attachments_backup_dubli;
--  2) из каждой группы одинаковых строк оставляет самую раннюю (по id),
--     остальные удаляет;
--  3) ставит на таблицу ограничение уникальности, чтобы дубли больше
--     не появлялись.
--
--  Третий шаг и есть настоящее лечение. Пока его нет, повторная
--  загрузка выгрузки или двойное нажатие в боте снова положат строку
--  рядом — и чистку придётся повторять.
--
--  ЧЕГО НЕ ДЕЛАЕТ
--  Не трогает строки без агента или без дня визита: они не дубли, а
--  неполные записи, и разбираться с ними надо отдельно.
--
--  КАК ЗАПУСКАТЬ — целым файлом, одной командой:
--
--      psql "ВАША_СТРОКА_ПОДКЛЮЧЕНИЯ" -1 -f dubli_ubrat.sql
--
--  Флаг -1 выполняет файл одной транзакцией: при ошибке на любом шаге
--  база останется ровно как была.
--
--  В консоли Railway — вставляйте целиком, не по частям.
--
-- =====================================================================


-- ---------- ШАГ 1. Копия ----------
-- Если таблица уже есть, запрос упадёт — это нарочно: значит копия от
-- прошлого запуска ещё лежит, и её надо сначала посмотреть
CREATE TABLE attachments_backup_dubli AS
SELECT *, now() AS snapshot_at FROM attachments;


-- ---------- ШАГ 2. Удаление ----------
-- Остаётся строка с наименьшим id — самая ранняя из одинаковых
DELETE FROM attachments a
 USING attachments b
 WHERE a.point_code = b.point_code
   AND upper(a.agent) = upper(b.agent)
   AND a.visit_day = b.visit_day
   AND a.id > b.id
   AND a.agent IS NOT NULL AND btrim(a.agent) <> ''
   AND a.visit_day IS NOT NULL AND btrim(a.visit_day) <> '';


-- ---------- ШАГ 3. Приведение логина к одному виду ----------
-- Система везде работает с логином в верхнем регистре. Пока в таблице
-- лежит 'or0111', ограничение ниже его пропустит рядом с 'OR0111',
-- и дубль вернётся с другой стороны
UPDATE attachments
   SET agent = upper(btrim(agent))
 WHERE agent IS NOT NULL
   AND agent <> upper(btrim(agent));


-- ---------- ШАГ 4. Защита от новых ----------
-- Частичное: строки без агента или без дня под правило не попадают,
-- иначе ограничение запретило бы хранить неполные записи
CREATE UNIQUE INDEX IF NOT EXISTS uniq_attachments_tochka_agent_den
    ON attachments (point_code, agent, visit_day)
 WHERE agent IS NOT NULL AND visit_day IS NOT NULL;


-- ---------- ШАГ 5. Проверка ----------
-- Обе цифры должны быть нулями
SELECT
    (SELECT COALESCE(sum(n - 1), 0) FROM (
        SELECT count(*) AS n FROM attachments
         WHERE agent IS NOT NULL AND btrim(agent) <> ''
           AND visit_day IS NOT NULL AND btrim(visit_day) <> ''
         GROUP BY point_code, upper(agent), visit_day
        HAVING count(*) > 1) x)                      AS ostalos_dublei,
    (SELECT count(*) FROM attachments
      WHERE agent IS NOT NULL AND agent <> upper(btrim(agent)))
                                                     AS ostalos_raznyi_registr,
    (SELECT count(*) FROM attachments_backup_dubli)  AS bylo_strok,
    (SELECT count(*) FROM attachments)               AS stalo_strok;


-- =====================================================================
--  ЕСЛИ РЕЗУЛЬТАТ НЕ ТОТ — вернуть всё как было:
--
--      BEGIN;
--      DROP INDEX IF EXISTS uniq_attachments_tochka_agent_den;
--      DELETE FROM attachments;
--      INSERT INTO attachments (id, point_code, point_name, agent_brand,
--                               agent, visit_day, created_at)
--      SELECT id, point_code, point_name, agent_brand, agent, visit_day,
--             created_at FROM attachments_backup_dubli;
--      SELECT setval('attachments_id_seq', (SELECT max(id) FROM attachments));
--      COMMIT;
--
--  Когда убедились, что всё верно, копию можно убрать:
--      DROP TABLE attachments_backup_dubli;
--
--  ПОСЛЕ
--  Выкатите новый api.py: там дни визита считаются по разным дням, а
--  не по числу строк. Без него панель и на чистых данных показывала бы
--  «2 дня» там, где день один.
-- =====================================================================
