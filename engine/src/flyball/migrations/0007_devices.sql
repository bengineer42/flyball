-- 0007: the device model.
--
-- A session now records *devices* (the things in the rig, each with a driver
-- and its config), *signals* (one named value of one quantity on one device,
-- addressed `device[.namespace…].signal`, with the access, unit and bands it
-- had), *write states* (what a writable signal was set to, when), and
-- *controllers* (a publishing signal regulated through a writable one, named
-- by the signal it drives). A sample is still the values under one node at one
-- instant, keyed by the device and the writer's per-device `seq`; a reading is
-- one value of one sample, by signal.
--
-- What the old rows become:
--   source            -> device: address = the source's name, driver = its kind,
--                        label kept, no config (none was recorded)
--   channel           -> signal: address = `source.measurand`, quantity and unit
--                        from the measurand, access "rp" (a channel was read and
--                        published; nothing else was recorded of it)
--   sample, reading   -> the same rows, keyed by the device and the signal
--   actuator          -> device: address = the actuator's name, driver = its
--                        kind, config kept -- unless a source already has that
--                        name, in which case the actuator row is dropped (the
--                        rig never allowed the clash; the schema did)
--   loop              -> controller: name = the loop's (its actuator's) name,
--                        source = the channel's address, law and feedforward kept
--   tick              -> tick, keyed by the controller's name
--   tuning.loop       -> tuning.controller (the column, renamed)
--
-- What cannot map and is dropped: an actuator had no signal, so no `write`
-- declaration and no write state is made for it -- its demands were never
-- recorded as such, only as the loop's ticks, which are kept. Every session
-- keeps its samples, readings, ticks, events and spans.

-- region New declarations

CREATE TABLE device (
    session_id  INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    id          INTEGER NOT NULL,           -- small int, stable within the session
    address     TEXT    NOT NULL,           -- the device's name: "hum_sensors"
    driver      TEXT,                       -- the driver tag: "sht4x_set"; informational
    config      TEXT,                       -- JSON: the driver config it was built from
    label       TEXT,                       -- display name
    PRIMARY KEY (session_id, id),
    UNIQUE (session_id, address)
);

-- One signal of one device, as declared for this session: what was recorded
-- of it is self-describing without the rig file that made it.
CREATE TABLE signal (
    session_id  INTEGER NOT NULL,
    id          INTEGER NOT NULL,
    device_id   INTEGER NOT NULL,
    address     TEXT    NOT NULL,           -- full: "hum_sensors.dry.humidity"
    quantity    TEXT    NOT NULL,           -- "humidity"
    unit        TEXT    NOT NULL,           -- "%RH"
    access      TEXT    NOT NULL,           -- wire form: "rp", "w", "rpw"
    dtype       TEXT    NOT NULL,           -- "float"
    shape       TEXT    NOT NULL,           -- JSON: [] for a scalar
    label       TEXT,
    range       TEXT,                       -- JSON [lo, hi] or NULL, likewise below
    precision   INTEGER,
    warn        TEXT,
    alarm       TEXT,
    limits      TEXT,                       -- what a demand is clamped to (W)
    PRIMARY KEY (session_id, id),
    UNIQUE (session_id, address),
    FOREIGN KEY (session_id, device_id) REFERENCES device(session_id, id) ON DELETE CASCADE
);

-- The signals whose writes this session recorded, with the driver behind them.
CREATE TABLE write (
    session_id  INTEGER NOT NULL,
    signal_id   INTEGER NOT NULL,
    driver      TEXT,                       -- informational, as device.driver
    limits      TEXT,                       -- JSON [lo, hi] or NULL
    PRIMARY KEY (session_id, signal_id),
    FOREIGN KEY (session_id, signal_id) REFERENCES signal(session_id, id) ON DELETE CASCADE
);

-- What a writable signal was set to: one row per commit that touched it.
CREATE TABLE write_state (
    session_id  INTEGER NOT NULL,
    signal_id   INTEGER NOT NULL,
    offset_ns   INTEGER NOT NULL,           -- offset from session.start_ns
    value       REAL,                       -- after limits
    requested   REAL,                       -- what was asked for, if it differed
    at_limit    TEXT,                       -- "low", "high" or NULL
    controller  TEXT,                       -- the controller driving it, if any
    PRIMARY KEY (session_id, signal_id, offset_ns),
    FOREIGN KEY (session_id, signal_id) REFERENCES write(session_id, signal_id) ON DELETE CASCADE
) WITHOUT ROWID;

-- A controller as wired for this session: named by the signal it drives,
-- regulating `source`. tick is its data.
CREATE TABLE controller (
    session_id  INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,           -- the target's address: "heaters.heater1"
    source      TEXT    NOT NULL,           -- the regulated signal's address
    law         TEXT,                       -- JSON: the law it started with
    feedforward TEXT,                       -- JSON: the feedforward config
    PRIMARY KEY (session_id, name)
);

-- A tuning is made for or on a controller now.
ALTER TABLE tuning RENAME COLUMN loop TO controller;

-- endregion

-- region Data, re-keyed

CREATE TABLE sample_v7 (
    session_id  INTEGER NOT NULL,
    device_id   INTEGER NOT NULL,
    seq         INTEGER NOT NULL,           -- the writer's counter per device
    node        TEXT    NOT NULL,           -- the address the sample was on: the device or a namespace
    offset_ns   INTEGER NOT NULL,
    PRIMARY KEY (session_id, device_id, seq),
    FOREIGN KEY (session_id, device_id) REFERENCES device(session_id, id) ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE reading_v7 (
    session_id  INTEGER NOT NULL,
    device_id   INTEGER NOT NULL,
    seq         INTEGER NOT NULL,
    signal_id   INTEGER NOT NULL,
    offset_ns   INTEGER NOT NULL,
    value       REAL    NOT NULL,
    PRIMARY KEY (session_id, device_id, seq, signal_id),
    FOREIGN KEY (session_id, device_id, seq) REFERENCES sample_v7(session_id, device_id, seq) ON DELETE CASCADE,
    FOREIGN KEY (session_id, signal_id) REFERENCES signal(session_id, id) ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE tick_v7 (
    session_id           INTEGER NOT NULL,
    controller           TEXT    NOT NULL,
    offset_ns            INTEGER NOT NULL,
    mode                 TEXT    NOT NULL,
    reading              REAL,
    setpoint             REAL,
    correction           REAL    NOT NULL,
    demand               REAL,
    expected             REAL,
    delivered_correction REAL,
    PRIMARY KEY (session_id, controller, offset_ns),
    FOREIGN KEY (session_id, controller) REFERENCES controller(session_id, name) ON DELETE CASCADE
) WITHOUT ROWID;

-- endregion

-- region Carry the old rows over

INSERT INTO device (session_id, id, address, driver, config, label)
    SELECT session_id, id, name, kind, NULL, label FROM source;

-- An actuator becomes a device after the sources, numbered on from them.
INSERT INTO device (session_id, id, address, driver, config, label)
    SELECT a.session_id,
           COALESCE((SELECT MAX(id) FROM source s WHERE s.session_id = a.session_id), 0)
               + ROW_NUMBER() OVER (PARTITION BY a.session_id ORDER BY a.name),
           a.name, a.kind, a.config, NULL
    FROM actuator a
    WHERE NOT EXISTS (SELECT 1 FROM source s WHERE s.session_id = a.session_id AND s.name = a.name);

-- Which (source, measurand) pair became which signal, for the readings.
CREATE TEMP TABLE signal_map AS
    SELECT c.session_id, c.source_id, c.measurand_id,
           ROW_NUMBER() OVER (PARTITION BY c.session_id ORDER BY c.source_id, c.measurand_id) AS signal_id
    FROM channel c;

INSERT INTO signal (session_id, id, device_id, address, quantity, unit, access, dtype, shape, label)
    SELECT m.session_id, m.signal_id, m.source_id, s.name || '.' || q.name, q.name, q.unit,
           'rp', 'float', '[]', q.label
    FROM signal_map m
    JOIN source s ON s.session_id = m.session_id AND s.id = m.source_id
    JOIN measurand q ON q.session_id = m.session_id AND q.id = m.measurand_id;

INSERT INTO sample_v7 (session_id, device_id, seq, node, offset_ns)
    SELECT p.session_id, p.source_id, p.seq, s.name, p.offset_ns
    FROM sample p JOIN source s ON s.session_id = p.session_id AND s.id = p.source_id;

INSERT INTO reading_v7 (session_id, device_id, seq, signal_id, offset_ns, value)
    SELECT r.session_id, r.source_id, r.seq, m.signal_id, r.offset_ns, r.value
    FROM reading r
    JOIN signal_map m ON m.session_id = r.session_id AND m.source_id = r.source_id
                     AND m.measurand_id = r.measurand_id;

INSERT INTO controller (session_id, name, source, law, feedforward)
    SELECT l.session_id, l.name, s.name || '.' || q.name, l.config, l.feedforward
    FROM loop l
    JOIN source s ON s.session_id = l.session_id AND s.id = l.source_id
    JOIN measurand q ON q.session_id = l.session_id AND q.id = l.measurand_id;

INSERT INTO tick_v7 (session_id, controller, offset_ns, mode, reading, setpoint, correction,
                     demand, expected, delivered_correction)
    SELECT session_id, loop, offset_ns, mode, reading, setpoint, correction,
           demand, expected, delivered_correction
    FROM tick;

DROP TABLE signal_map;

-- endregion

-- region Retire the old tables: children before parents, so no reference dangles

DROP TABLE reading;
DROP TABLE sample;
DROP TABLE tick;
DROP TABLE loop;
DROP TABLE channel;
DROP TABLE actuator;
DROP TABLE measurand;
DROP TABLE source;

ALTER TABLE sample_v7 RENAME TO sample;
ALTER TABLE reading_v7 RENAME TO reading;
ALTER TABLE tick_v7 RENAME TO tick;

-- Per-signal time series access, covering: "hum_sensors.dry.humidity between
-- t1 and t2" is one range scan of this index, value included, no table or join.
CREATE INDEX reading_by_signal ON reading (session_id, signal_id, offset_ns, value);

-- "Everything between t1 and t2" across devices, for export.
CREATE INDEX sample_by_time ON sample (session_id, offset_ns);

-- endregion
