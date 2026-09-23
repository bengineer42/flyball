-- 0018: the feedforward that passes the setpoint straight through is `identity`.
--
-- Its type was `setpoint`, which also names the value it maps; the union of
-- feedforward configs no longer knows that type, so a stored rig version whose
-- controller named it would no longer load (`--resume`, a restart from the
-- head, a restore). Each controller's `feedforward.type` is rewritten in
-- place, keeping the controllers' order.

UPDATE rig_version
SET document = json_set(
    document,
    '$.controllers',
    (
        SELECT json_group_object(
            c.key,
            CASE
                WHEN json_extract(c.value, '$.feedforward.type') = 'setpoint'
                THEN json_set(c.value, '$.feedforward.type', 'identity')
                ELSE json(c.value)
            END
        )
        FROM json_each(rig_version.document, '$.controllers') AS c
    )
)
WHERE json_valid(document) AND json_type(document, '$.controllers') = 'object';
