-- 0019: an event's `kind` is its `code`, and its `level` its `severity` (C15, signal faults).
--
-- The column is renamed in place. The severity, stored in each event's JSON
-- `detail` as `logging`'s number (10, 20, 30, 40), becomes the lowercase string
-- the wire carries (`debug`, `info`, `warning`, `error`), under `severity`; a
-- detail with no numeric `level` is left as it was.

ALTER TABLE event RENAME COLUMN kind TO code;

UPDATE event
SET detail = json_remove(
    json_set(
        detail,
        '$.severity',
        CASE json_extract(detail, '$.level')
            WHEN 10 THEN 'debug'
            WHEN 20 THEN 'info'
            WHEN 30 THEN 'warning'
            WHEN 40 THEN 'error'
            ELSE CAST(json_extract(detail, '$.level') AS TEXT)
        END
    ),
    '$.level'
)
WHERE json_valid(detail) AND json_type(detail, '$.level') = 'integer';
