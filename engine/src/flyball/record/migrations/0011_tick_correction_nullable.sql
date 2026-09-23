-- 0011: a tick's correction may be NULL.
--
-- A law whose integral went NaN (a NaN reading, a NaN gain) still steps, and
-- its tick is exactly the one worth keeping. SQLite stores a NaN REAL as
-- NULL, so `correction REAL NOT NULL` turned that tick into an IntegrityError
-- that ended the recording. NULL now means "the law's output was not a
-- number"; every other column already allowed it. SQLite cannot drop a NOT
-- NULL in place, so the table is rebuilt with the same key and foreign key.

CREATE TABLE tick_v11 (
    session_id           INTEGER NOT NULL,
    controller           TEXT    NOT NULL,
    offset_ns            INTEGER NOT NULL,
    mode                 TEXT    NOT NULL,
    reading              REAL,
    setpoint             REAL,
    correction           REAL,
    demand               REAL,
    expected             REAL,
    delivered_correction REAL,
    PRIMARY KEY (session_id, controller, offset_ns),
    FOREIGN KEY (session_id, controller) REFERENCES controller(session_id, name) ON DELETE CASCADE
) WITHOUT ROWID;

INSERT INTO tick_v11 (session_id, controller, offset_ns, mode, reading, setpoint, correction,
                      demand, expected, delivered_correction)
    SELECT session_id, controller, offset_ns, mode, reading, setpoint, correction,
           demand, expected, delivered_correction
    FROM tick;

DROP TABLE tick;
ALTER TABLE tick_v11 RENAME TO tick;
