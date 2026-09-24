-- 0001: the store's schema.
--
-- The baseline every store starts from (D-064: the migrations before the R1
-- rename were folded into this one, and a store they made is refused).
--
-- Times are integer nanoseconds. A bare `_ns` is on the rig's clock; a
-- `_utc_ns` is wall-clock time (D-084): the two read the same on hardware and
-- differ in a simulation, whose clock runs scaled or stepped. Within a session
-- times are offsets from the session's origin, so a row never depends on the
-- clock being right.
--
-- A session records what the rig had (*devices*, their *signals*, the
-- *controllers*) and what happened: a *sample* is the values under one node at
-- one instant, a *reading* one value of one sample, a *write state* what a
-- writable signal was set to, a *tick* one step of a controller, an *event*
-- anything non-numeric, a *span* the program structure laid over the timeline.
-- Tunings, programs, dashboards and rig versions outlive sessions. Nothing
-- here is specific to one application: names are data.

-- The applied version: one row, the last migration applied.
CREATE TABLE schema_version (
    version     INTEGER NOT NULL
);

-- What `PRAGMA application_id` says of a store made from this baseline ("flyb").
PRAGMA application_id = 1718384994;  -- 0x666C7962, `flyball.record.migrate.APPLICATION_ID`

-- region The rig, versioned

-- The rig as a versioned document: what the runner loaded, then every change
-- made to the running rig (a link or device added or removed, a controller
-- attached or detached, a simulation's knobs saved). Each row is the whole
-- rendered rig file, never a diff, so any version stands alone; a delta is
-- computed on read. Versions are a tree with a head: a change is a new row
-- whose parent is the head; restoring a version moves the head to it and
-- writes nothing, so a version restored and then changed branches from it.
CREATE TABLE rig_version (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    time_ns     INTEGER NOT NULL,           -- the rig's clock
    reason      TEXT    NOT NULL,           -- "loaded", "added device blender", ...
    files       TEXT,                       -- JSON: the files the runner loaded, for provenance
    document    TEXT    NOT NULL,           -- JSON: the rig file as it stood
    parent_id   INTEGER REFERENCES rig_version(id)
);

CREATE TABLE rig_head (
    one        INTEGER PRIMARY KEY CHECK (one = 1),   -- a single row
    version_id INTEGER NOT NULL REFERENCES rig_version(id)
);

-- endregion

-- region Sessions

-- One recording, or the scratch record. Owns everything in this region.
--
-- `kind` is "session" for a recording someone started, "scratch" for the
-- rolling record the runner keeps while nothing is recorded, trimmed to the
-- last `keep` of the rig's clock. Trimming deletes a scratch session's oldest
-- rows and moves its `start_ns` forward to the oldest row kept; offsets are
-- not rewritten: they stay relative to `origin_ns`, the start as it was
-- opened, and a read shifts them by `start_ns - origin_ns`. `pinned` exempts a
-- session from retention; `continues` names the session a rotated one carries
-- on from; `bytes` is the runner's latest estimate of what a scratch session
-- holds on disk.
CREATE TABLE session (
    id              INTEGER PRIMARY KEY,
    start_ns        INTEGER NOT NULL,       -- the rig's clock at the session's start
    end_ns          INTEGER,                -- NULL while open
    flyball_version TEXT,                   -- flyball version string
    config          TEXT,                   -- JSON: the rig config that was built
    hardware        TEXT,                   -- JSON: what was actually found on the bus
    details         TEXT,                   -- JSON: anything else worth keeping
    rig_version_id  INTEGER REFERENCES rig_version(id),  -- the version it started on
    kind            TEXT    NOT NULL DEFAULT 'session',
    origin_ns       INTEGER,
    pinned          INTEGER NOT NULL DEFAULT 0,
    continues       INTEGER REFERENCES session(id) ON DELETE SET NULL,
    bytes           INTEGER
);

CREATE INDEX session_by_kind ON session (kind, start_ns);

-- A device as the session had it: its driver and the config it was built
-- from, so what was recorded is self-describing without the rig file.
CREATE TABLE device (
    session_id  INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    id          INTEGER NOT NULL,           -- small int, stable within the session
    address     TEXT    NOT NULL,           -- the device's name: "hum_sensors"
    driver      TEXT,                       -- the driver type: "sht4x_set"; informational
    config      TEXT,                       -- JSON: the driver config it was built from
    label       TEXT,                       -- display name
    PRIMARY KEY (session_id, id),
    UNIQUE (session_id, address)
);

-- One signal of one device, as declared for this session: its access, unit
-- and bands as they were.
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
    warning     TEXT,
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
    offset_ns   INTEGER NOT NULL,           -- offset from the session's origin
    value       REAL,                       -- after limits
    requested   REAL,                       -- what was asked for, if it differed
    at_limit    TEXT,                       -- "low", "high" or NULL
    controller  TEXT,                       -- the controller driving it, if any
    PRIMARY KEY (session_id, signal_id, offset_ns),
    FOREIGN KEY (session_id, signal_id) REFERENCES write(session_id, signal_id) ON DELETE CASCADE
) WITHOUT ROWID;

-- One instant under one node, keyed by the device and the writer's counter
-- per device, so gaps are visible and replay cannot double-count (D-004).
CREATE TABLE sample (
    session_id  INTEGER NOT NULL,
    device_id   INTEGER NOT NULL,
    seq         INTEGER NOT NULL,           -- the writer's counter per device
    node        TEXT    NOT NULL,           -- the address the sample was on: the device or a namespace
    offset_ns   INTEGER NOT NULL,
    PRIMARY KEY (session_id, device_id, seq),
    FOREIGN KEY (session_id, device_id) REFERENCES device(session_id, id) ON DELETE CASCADE
) WITHOUT ROWID;

-- "Everything between t1 and t2" across devices, for export.
CREATE INDEX sample_by_time ON sample (session_id, offset_ns);

-- One value of one sample: the hot table. `offset_ns` is repeated from the
-- sample so a time-windowed read of one signal never joins.
--
-- A reading may have no value: it is still a reading (stored, and a chart
-- breaks at it). `flag` says what it is, one of two kinds of code, never both:
--
--   1-15, with value NULL (the no-value's quality):
--     1 invalid, 2 not_applicable, 3 stale, 4 stale(device_offline); 5-15 free.
--     Every other stale reason shares 3. `pending` writes no row.
--   16-31, with a value (a mark on it):
--     16 at_limit low, 17 at_limit high; 18-31 free.
--
-- A value without a mark has flag NULL. The CHECKs make the pairing hold.
CREATE TABLE reading (
    session_id  INTEGER NOT NULL,
    device_id   INTEGER NOT NULL,
    seq         INTEGER NOT NULL,
    signal_id   INTEGER NOT NULL,
    offset_ns   INTEGER NOT NULL,
    value       REAL,
    flag        INTEGER,
    PRIMARY KEY (session_id, device_id, seq, signal_id),
    FOREIGN KEY (session_id, device_id, seq) REFERENCES sample(session_id, device_id, seq) ON DELETE CASCADE,
    FOREIGN KEY (session_id, signal_id) REFERENCES signal(session_id, id) ON DELETE CASCADE,
    CHECK (flag IS NULL OR typeof(flag) = 'integer'),
    CHECK (CASE WHEN value IS NULL THEN coalesce(flag BETWEEN 1 AND 15, 0)
                ELSE coalesce(flag BETWEEN 16 AND 31, 1) END)
) WITHOUT ROWID;

-- Per-signal series, covering: "hum_sensors.dry.humidity between t1 and t2",
-- its breaks and marks included, is one range scan of this index.
CREATE INDEX reading_by_signal ON reading (session_id, signal_id, offset_ns, value, flag);

-- A controller as wired for this session: named by the signal it drives (its
-- output), regulating `measured`. `tick` is its data.
CREATE TABLE controller (
    session_id  INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,           -- the output's address: "heaters.heater1"
    measured    TEXT    NOT NULL,           -- the regulated signal's address
    law         TEXT,                       -- JSON: the law it started with
    feedforward TEXT,                       -- JSON: the feedforward config
    PRIMARY KEY (session_id, name)
);

-- One step of one controller: a row of related values, the controller's
-- contract. `correction` is NULL when the law's output was not a number
-- (a NaN is exactly the tick worth keeping). A controller following a moving
-- setpoint re-applies its feedforward between readings: that tick has no
-- reading, `reapplied` is 1 on it and its `measured_value` is NULL.
CREATE TABLE tick (
    session_id           INTEGER NOT NULL,
    controller           TEXT    NOT NULL,
    offset_ns            INTEGER NOT NULL,   -- offset from the session's origin
    mode                 TEXT    NOT NULL,
    measured_value       REAL,
    setpoint             REAL,
    correction           REAL,
    output_value         REAL,
    expected             REAL,
    delivered_correction REAL,
    reapplied            INTEGER NOT NULL DEFAULT 0 CHECK (reapplied IN (0, 1)),
    PRIMARY KEY (session_id, controller, offset_ns),
    FOREIGN KEY (session_id, controller) REFERENCES controller(session_id, name) ON DELETE CASCADE
) WITHOUT ROWID;

-- Non-numeric happenings: faults, mode changes, retunes, notes. A condition's
-- start and end are events told apart by `edge`: `raised`, `cleared`, or NULL
-- for a point event (a program step, a restore).
CREATE TABLE event (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    offset_ns   INTEGER NOT NULL,           -- offset from the session's origin
    subject     TEXT,                       -- what it is about: a device, a signal, a controller
    code        TEXT    NOT NULL,           -- "offline", "retune", "flag", ...
    details     TEXT,                       -- JSON: its severity, subject_kind, message, ...
    edge        TEXT
);

CREATE INDEX event_by_time ON event (session_id, offset_ns);

-- Program structure over the timeline: a program contains runs contains
-- commands; a note is a point span. The parent gives the nesting.
CREATE TABLE span (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    parent_id   INTEGER REFERENCES span(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL,           -- SpanKind wire value
    label       TEXT    NOT NULL,
    start_ns    INTEGER NOT NULL,           -- offset from the session's origin
    end_ns      INTEGER,                    -- NULL while open
    details     TEXT                        -- JSON: the command as written, its outcome
);

CREATE INDEX span_by_time ON span (session_id, start_ns);

-- endregion

-- region The library: versioned by name

-- A tuning is a law and its gains under a name a program can ask for. It
-- outlives sessions: `session_id` only records where one came from and is
-- cleared if that session is deleted. Tunings, programs and dashboards are
-- append-only: saving a name again adds a row, so the trail is visible;
-- readers want the newest per name.
CREATE TABLE tuning (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,           -- "pid_aggressive"
    law         TEXT    NOT NULL,           -- the law's type: "PID"
    config      TEXT    NOT NULL,           -- JSON: the law's constructor arguments
    created_ns  INTEGER NOT NULL,           -- the rig's clock
    session_id  INTEGER REFERENCES session(id) ON DELETE SET NULL,
    controller  TEXT,                       -- the controller it was made for or on, if any
    notes       TEXT                        -- JSON: the plant it was fitted to, the rule, a comment
);

CREATE INDEX tuning_by_name ON tuning (name, created_ns DESC);

-- A program is kept as the text it was written in -- YAML, TOML or JSON --
-- byte for byte, so comments and the author's layout survive. Nothing here is
-- parsed: a program that no longer validates is still a document worth keeping.
CREATE TABLE program (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,           -- "dry_then_hold"
    format      TEXT    NOT NULL,           -- "yaml", "toml" or "json": what `body` is written in
    body        TEXT    NOT NULL,           -- the document, verbatim
    created_ns  INTEGER NOT NULL,           -- the rig's clock
    notes       TEXT,                       -- JSON: what the author said about this version
    sha256      TEXT    NOT NULL            -- of body; identical re-saves are cheap to spot
);

CREATE INDEX program_by_name ON program (name, created_ns DESC);

-- What the UI shows and how, saved per rig.
CREATE TABLE dashboard (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,           -- "furnace_overview"
    rig         TEXT    NOT NULL,           -- the rig it was made for, by name
    body        TEXT    NOT NULL,           -- JSON: the dashboard document
    created_ns  INTEGER NOT NULL,           -- the rig's clock
    sha256      TEXT    NOT NULL            -- of body; identical re-saves are cheap to spot
);

CREATE INDEX dashboard_by_name ON dashboard (name, created_ns DESC);
CREATE INDEX dashboard_by_rig ON dashboard (rig, name);

-- endregion

-- region What outlives the process

-- Live values (C10(5), C11): one row per signal whose last written value a
-- restart restores: a `driver: values` entry's (`kind` 'value'), later a
-- driver setting that declares its config field (`kind` 'setting'). Not a
-- recording: the runner always opens the store, recording or not.
--
-- `value` and `initial` are JSON (a setting may be a str, a bool or an enum
-- member). `initial` is the rig file's value in force when the write happened:
-- on a restart the row is restored only while the file's `initial` still
-- equals it (else the file was edited since, and it wins) and its `unit` is
-- the signal's unit still (else a condition names it, and the file wins).
CREATE TABLE live_value (
    device          TEXT    NOT NULL,
    signal          TEXT    NOT NULL,       -- the path under the device: `dry_supply`
    kind            TEXT    NOT NULL CHECK (kind IN ('value', 'setting')),
    value           TEXT    NOT NULL,       -- JSON
    unit            TEXT,                   -- the unit symbol at write time; NULL: none
    initial         TEXT,                   -- JSON: the rig file's value in force then
    config_field    TEXT,                   -- a setting's DriverConfig field; NULL for a value
    actor           TEXT,                   -- JSON {principal, kind, via, sid, message}; NULL: not known
    written_utc_ns  INTEGER NOT NULL,       -- wall-clock time of the write
    head_version    INTEGER REFERENCES rig_version(id) ON DELETE SET NULL,  -- the rig version then
    PRIMARY KEY (device, signal)
);

-- Latches (D-048..D-052): one row per cause held, the rig stop (`stop`) or a
-- controller's `on_fault` action (`on_fault:<controller>`). Written when the
-- latch is set and deleted by its Reset; a runner started on this store
-- restores every row, and a rig stop's re-applies the stop before serving.
CREATE TABLE latch (
    cause       TEXT    PRIMARY KEY,
    subjects    TEXT    NOT NULL,           -- JSON [{subject_kind, subject}]
    actor       TEXT    NOT NULL,           -- JSON {principal, kind, via, sid, message}: who set it
    at_utc_ns   INTEGER NOT NULL,           -- wall-clock time it was set
    reason      TEXT    NOT NULL DEFAULT '',  -- what they said
    action      TEXT    NOT NULL DEFAULT ''   -- a fault's action; '' for the rig stop
);

-- The runner's action audit: one row per action on the rig -- every request
-- whose verb is not read, and every stop, the break-glass signal included
-- (`flyball.record.audit`). It names no session: retention, trimming and
-- deleting a session never touch it. `boot` is one runner process and `seq`
-- counts its actions from 1, so a gap is an action the store never took (the
-- runner logged it instead). Rows are never changed or deleted: the triggers
-- refuse.
--
-- `writes` is JSON, for a demand: {address: {old, requested, applied}}.
-- `details` is JSON too: a stop's reason. `status` is NULL for an action that
-- was no request.
CREATE TABLE audit (
    id          INTEGER PRIMARY KEY,
    time_utc_ns INTEGER NOT NULL,           -- wall-clock time it was asked for
    boot        TEXT    NOT NULL,
    seq         INTEGER NOT NULL,
    principal   TEXT    NOT NULL,
    name        TEXT    NOT NULL DEFAULT '',
    sid         TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    via         TEXT    NOT NULL,
    cip         TEXT    NOT NULL,
    scheme      TEXT    NOT NULL DEFAULT '',
    method      TEXT    NOT NULL,
    route       TEXT    NOT NULL,
    path        TEXT    NOT NULL,
    status      INTEGER,
    outcome     TEXT    NOT NULL,
    request_id  TEXT    NOT NULL DEFAULT '',
    writes      TEXT,
    details     TEXT,
    UNIQUE (boot, seq)
);

CREATE INDEX audit_by_time ON audit (time_utc_ns);

CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit
BEGIN
    SELECT RAISE(ABORT, 'the audit is append-only: rows are never changed');
END;

CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit
BEGIN
    SELECT RAISE(ABORT, 'the audit is append-only: rows are never deleted');
END;

-- endregion
