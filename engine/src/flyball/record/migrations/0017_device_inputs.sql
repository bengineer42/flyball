-- 0017: a device's `bound:` is its `inputs:`.
--
-- The rig-file key naming what a device follows on another is `inputs`,
-- keyed by the input's name, and `DeviceEntry` keeps any unknown key as a
-- driver field, so a stored rig version written with `bound` would hand it to
-- the driver and fail to load (`--resume`, a restart from the head, a
-- restore). Every device entry has the key renamed in place, keeping the
-- devices' order; an entry without `bound` is left as it is.

UPDATE rig_version
SET document = json_set(
    document,
    '$.devices',
    (
        SELECT json_group_object(
            d.key,
            CASE
                WHEN json_type(d.value, '$.bound') IS NOT NULL
                THEN json_remove(
                    json_set(d.value, '$.inputs', json_extract(d.value, '$.bound')),
                    '$.bound'
                )
                ELSE json(d.value)
            END
        )
        FROM json_each(rig_version.document, '$.devices') AS d
    )
)
WHERE json_valid(document) AND json_type(document, '$.devices') = 'object';
