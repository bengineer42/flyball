-- 0023: live values that outlive the process (C10(5), C11).
--
-- One row per signal whose last written value a restart restores: a
-- `driver: values` entry's (`kind` 'value'), and later a driver setting that
-- declares its config field (`kind` 'setting', C11). Not a recording: the runner
-- always opens the store, recording or not.
--
-- `value` and `initial` are JSON (a setting may be a str, a bool or an enum
-- member). `initial` is the rig file's value in force when the write happened:
-- on a restart the row is restored only while the file's current `initial`
-- still equals it (else the file was edited since, and it wins) and its `unit`
-- is the signal's unit still (else a condition names it, and the file wins).
-- `writer` is who wrote it (the principal's `sub`), `written_ns` when (wall
-- time, ns since the epoch), `head_version` the rig version in force then.
-- `config_field` names the driver config field behind a setting; NULL for a value.
CREATE TABLE live_value (
    device        TEXT    NOT NULL,
    signal        TEXT    NOT NULL,            -- the path under the device: `dry_supply`
    kind          TEXT    NOT NULL CHECK (kind IN ('value', 'setting')),
    value         TEXT    NOT NULL,            -- JSON
    unit          TEXT,                        -- the unit symbol at write time; NULL: none
    initial       TEXT,                        -- JSON: the rig file's value in force then
    config_field  TEXT,                        -- a setting's DriverConfig field; NULL for a value
    writer        TEXT,
    written_ns    INTEGER NOT NULL,
    head_version  INTEGER REFERENCES rig_version(id) ON DELETE SET NULL,
    PRIMARY KEY (device, signal)
);
