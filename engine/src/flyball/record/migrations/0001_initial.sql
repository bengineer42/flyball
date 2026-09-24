-- 0001: initial schema for the generic rig.
--
-- Shape follows the readings model (D-004): a *source* declares *measurands*
-- (each with a fixed unit); a *sample* is every measurand of one source at one
-- instant; a *reading* is one value of one sample. Loops write a *tick* per
-- step; anything non-numeric is an *event*. Spans are the program structure
-- laid over the timeline. All times are integer nanoseconds; within a session
-- they are offsets from the session start so a row never depends on the wall
-- clock being right.
--
-- Rows are append-only while a session is open. Nothing here is humidity-
-- specific: the measurand names are data.

CREATE TABLE schema_version (
    version     INTEGER NOT NULL
);

-- One run of the runner, or one explicit recording. Owns everything below.
CREATE TABLE session (
    id          INTEGER PRIMARY KEY,
    start_ns    INTEGER NOT NULL,           -- wall clock at session start, ns since epoch
    end_ns      INTEGER,                    -- NULL while open
    version     TEXT,                       -- flyball version string
    config      TEXT,                       -- JSON: the rig config that was built
    hardware    TEXT,                       -- JSON: what was actually found on the bus
    details     TEXT                        -- JSON: anything else worth keeping
);

-- What was measured. Interned per session so a trace is self-describing
-- without the process that wrote it.
CREATE TABLE measurand (
    session_id  INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    id          INTEGER NOT NULL,           -- small int, stable within the session
    name        TEXT    NOT NULL,           -- wire name: "humidity"
    unit        TEXT    NOT NULL,           -- "%RH"
    label       TEXT,                       -- display name
    PRIMARY KEY (session_id, id),
    UNIQUE (session_id, name)
);

-- Something that emits samples: a sensor, a fusion, a loop's outputs.
CREATE TABLE source (
    session_id  INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    id          INTEGER NOT NULL,
    name        TEXT    NOT NULL,           -- "process", "dry", "loop:humidity"
    kind        TEXT,                       -- "sht4x", "loop", "fused" -- informational
    PRIMARY KEY (session_id, id),
    UNIQUE (session_id, name)
);

-- Which measurands each source declared. A channel is (source, measurand).
CREATE TABLE channel (
    session_id  INTEGER NOT NULL,
    source_id   INTEGER NOT NULL,
    measurand_id INTEGER NOT NULL,
    PRIMARY KEY (session_id, source_id, measurand_id),
    FOREIGN KEY (session_id, source_id)   REFERENCES source(session_id, id)   ON DELETE CASCADE,
    FOREIGN KEY (session_id, measurand_id) REFERENCES measurand(session_id, id) ON DELETE CASCADE
);

-- One instant from one source. seq is the source's own counter, so gaps are
-- visible and replay cannot double-count (D-004).
CREATE TABLE sample (
    session_id  INTEGER NOT NULL,
    source_id   INTEGER NOT NULL,
    seq         INTEGER NOT NULL,
    offset_ns     INTEGER NOT NULL,           -- offset from session.start_ns
    PRIMARY KEY (session_id, source_id, seq),
    FOREIGN KEY (session_id, source_id) REFERENCES source(session_id, id) ON DELETE CASCADE
) WITHOUT ROWID;

-- One value of one sample. The hot table: one row per measurand per sample.
-- offset_ns is repeated from sample so a time-windowed read of one channel
-- never joins; the covering index below answers every series query alone.
CREATE TABLE reading (
    session_id  INTEGER NOT NULL,
    source_id   INTEGER NOT NULL,
    seq         INTEGER NOT NULL,
    measurand_id INTEGER NOT NULL,
    offset_ns   INTEGER NOT NULL,
    value       REAL    NOT NULL,
    PRIMARY KEY (session_id, source_id, seq, measurand_id),
    FOREIGN KEY (session_id, source_id, seq) REFERENCES sample(session_id, source_id, seq) ON DELETE CASCADE
) WITHOUT ROWID;

-- Per-channel time series access, covering: "process.humidity between t1 and
-- t2" is one range scan of this index, value included, no table or join.
CREATE INDEX reading_by_channel ON reading (session_id, source_id, measurand_id, offset_ns, value);

-- "Everything between t1 and t2" across sources, for export.
CREATE INDEX sample_by_time ON sample (session_id, offset_ns);

-- Something the rig drives. Exists whether or not a loop drives it -- pumps
-- can be commanded by hand -- and its own configuration lives here.
CREATE TABLE actuator (
    session_id  INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,           -- "pumps"
    kind        TEXT    NOT NULL,           -- "DualPumps" -- informational
    config      TEXT,                       -- JSON: max flows, deadbands, ...
    PRIMARY KEY (session_id, name)
);

-- A control loop as wired for this session: the actuator it drives (whose
-- name is the loop's) and the channel it regulates. tick is its data.
CREATE TABLE loop (
    session_id  INTEGER NOT NULL,
    name        TEXT    NOT NULL,           -- = actuator.name
    source_id   INTEGER NOT NULL,           -- the CV channel...
    measurand_id INTEGER NOT NULL,           -- ...and so its unit
    config      TEXT,                       -- JSON: the law it started with
    PRIMARY KEY (session_id, name),
    FOREIGN KEY (session_id, name) REFERENCES actuator(session_id, name) ON DELETE CASCADE,
    FOREIGN KEY (session_id, source_id, measurand_id)
        REFERENCES channel(session_id, source_id, measurand_id) ON DELETE CASCADE
);

-- One control step of one loop. Kept separate from reading because a tick is a
-- row of related values, not independent channels, and because its columns
-- are the loop's contract, not a source's declaration.
CREATE TABLE tick (
    session_id           INTEGER NOT NULL,
    loop                 TEXT    NOT NULL,
    offset_ns            INTEGER NOT NULL,   -- offset from session.start_ns
    mode                 TEXT    NOT NULL,   -- LoopMode wire value
    reading              REAL,               -- the CV value the step used
    setpoint             REAL,
    correction           REAL    NOT NULL,
    demand               REAL,
    expected             REAL,
    delivered_correction REAL,
    PRIMARY KEY (session_id, loop, offset_ns),
    FOREIGN KEY (session_id, loop) REFERENCES loop(session_id, name) ON DELETE CASCADE
) WITHOUT ROWID;

-- Non-numeric happenings: faults, mode changes, retunes, operator notes.
CREATE TABLE event (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    offset_ns   INTEGER NOT NULL,           -- offset from session.start_ns
    subject     TEXT,                       -- what it is about: a device, a signal, a controller
    kind        TEXT    NOT NULL,           -- "read-failed", "retune", "flag", ...
    details     TEXT                        -- JSON or plain text
);
CREATE INDEX event_by_time ON event (session_id, offset_ns);

-- Program structure over the timeline: a program contains runs contains
-- commands; a note is a point span. parent gives the nesting.
CREATE TABLE span (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    parent_id   INTEGER REFERENCES span(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL,           -- SpanKind wire value
    label       TEXT    NOT NULL,
    start_ns    INTEGER NOT NULL,           -- offset from session.start_ns
    end_ns      INTEGER,                    -- NULL while open
    details     TEXT                        -- JSON: the command as written, its outcome
);
CREATE INDEX span_by_time ON span (session_id, start_ns);
