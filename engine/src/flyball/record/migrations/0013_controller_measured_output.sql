-- 0013: a controller's `signal` is its `measured` signal; its target is its `output`.
--
-- The rig-file key `signal:` under `controllers` is now `measured:`, and
-- `ControllerEntry` forbids unknown keys, so a stored rig version written
-- before the rename would no longer load (`--resume`, a restart from the
-- head, a restore). Every `rig_version.document` has the key renamed in place,
-- keeping the controllers' order; a controller entry without `signal` is left
-- as it is. `session.config` is the record of what a past session built and
-- is never loaded again, so it keeps the spelling it was written with.
--
-- The recorded columns follow the same words: a tick's `reading` and `demand`
-- are its `measured_value` and `output_value`, and a controller's `source` its `measured`.

UPDATE rig_version
SET document = json_set(
    document,
    '$.controllers',
    (
        SELECT json_group_object(
            c.key,
            CASE
                WHEN json_type(c.value, '$.signal') IS NOT NULL
                THEN json_remove(
                    json_set(c.value, '$.measured', json_extract(c.value, '$.signal')),
                    '$.signal'
                )
                ELSE json(c.value)
            END
        )
        FROM json_each(rig_version.document, '$.controllers') AS c
    )
)
WHERE json_valid(document) AND json_type(document, '$.controllers') = 'object';

ALTER TABLE tick RENAME COLUMN reading TO measured_value;
ALTER TABLE tick RENAME COLUMN demand TO output_value;
ALTER TABLE controller RENAME COLUMN source TO measured;
