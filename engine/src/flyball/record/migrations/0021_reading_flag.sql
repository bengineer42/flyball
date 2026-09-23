-- 0021: a reading may have no value, and a value may carry a mark (A2, signal faults).
--
-- `value` becomes nullable: a reading with no value is a real reading -- it is
-- stored, and a chart breaks at it. `flag` says what it is, and a row carries
-- one of two kinds of code, never both:
--
--   1-15, with value NULL (the no-value's quality):
--     1 invalid, 2 not_applicable, 3 stale, 4 stale(device_offline); 5-15 free.
--     Every other stale reason shares 3. `pending` writes no row.
--   16-31, with a value (a mark on it):
--     16 at_limit low, 17 at_limit high; 18-31 free.
--
-- A value row without a mark has flag NULL. The CHECKs make the pairing hold:
-- a NULL value always has a no-value code, a value never has one.
--
-- SQLite cannot drop a NOT NULL or add a CHECK in place, so the table is
-- rebuilt with the same key and foreign keys, as 0011 did for `tick`, and
-- its covering index with it: `reading_by_signal` carries `flag` too, so a
-- series with its breaks and marks is still one range scan of the index.

CREATE TABLE reading_v21 (
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

INSERT INTO reading_v21 (session_id, device_id, seq, signal_id, offset_ns, value, flag)
    SELECT session_id, device_id, seq, signal_id, offset_ns, value, NULL
    FROM reading;

DROP TABLE reading;
ALTER TABLE reading_v21 RENAME TO reading;

CREATE INDEX reading_by_signal ON reading (session_id, signal_id, offset_ns, value, flag);
