-- 0016: a device's driver fields sit flat beside `driver:`; there is no `config:`.
--
-- `DeviceEntry` refuses a nested `config` key now, so a stored rig version
-- written with the layered form would no longer load (`--resume`, a restart
-- from the head, a restore). Every device entry that has a `config` object has
-- its fields moved up beside the envelope, keeping the devices' order; an
-- entry without one is left as it is. `session.config` is the record of what
-- a past session built and is never loaded again, so it keeps its shape.

UPDATE rig_version
SET document = json_set(
    document,
    '$.devices',
    (
        SELECT json_group_object(
            d.key,
            CASE
                WHEN json_type(d.value, '$.config') = 'object'
                THEN json_patch(json_remove(d.value, '$.config'), json_extract(d.value, '$.config'))
                ELSE json(d.value)
            END
        )
        FROM json_each(rig_version.document, '$.devices') AS d
    )
)
WHERE json_valid(document) AND json_type(document, '$.devices') = 'object';
