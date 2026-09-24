-- 0024: a blender's supply humidities are inputs bound to numbers (C12).
--
-- An input has no default any more: every input a driver declares is bound,
-- to an address or to a number. The humidity application's blender
-- (`dual_pump_blender`) took its supply humidities from `supply: {dry, wet}`
-- when unbound, else from built-in defaults (dry 0 %RH, wet 100 %RH); both are
-- gone, so a stored rig version with either would no longer load (`--resume`,
-- a restart from the head, a restore). Each such device's `inputs` gets `dry`
-- and `wet`: kept where bound already, else the `supply` number, else the old
-- default; `supply` is removed. The devices' order is kept.

UPDATE rig_version
SET document = json_set(
    document,
    '$.devices',
    (
        SELECT json_group_object(
            d.key,
            CASE
                WHEN json_extract(d.value, '$.driver') = 'dual_pump_blender'
                THEN json_remove(
                    json_set(
                        d.value,
                        '$.inputs',
                        json_set(
                            coalesce(json_extract(d.value, '$.inputs'), json('{}')),
                            '$.dry',
                            coalesce(
                                json_extract(d.value, '$.inputs.dry'),
                                json_extract(d.value, '$.supply.dry'),
                                0.0
                            ),
                            '$.wet',
                            coalesce(
                                json_extract(d.value, '$.inputs.wet'),
                                json_extract(d.value, '$.supply.wet'),
                                100.0
                            )
                        )
                    ),
                    '$.supply'
                )
                ELSE json(d.value)
            END
        )
        FROM json_each(rig_version.document, '$.devices') AS d
    )
)
WHERE json_valid(document) AND json_type(document, '$.devices') = 'object';
