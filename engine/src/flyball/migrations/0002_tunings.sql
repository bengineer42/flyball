-- 0002: named tunings.
--
-- A tuning is a law and its gains under a name a program can ask for
-- ("pid-aggressive"). They outlive sessions -- a scientist builds a library of
-- them -- so the table is not owned by session; session_id only records where
-- one came from, and is cleared if that session is deleted. Saving a name
-- again adds a row rather than overwriting, so a retune trail is visible;
-- readers want the newest per name.

CREATE TABLE tuning (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,           -- "pid-aggressive"
    law         TEXT    NOT NULL,           -- law tag: "PID"
    config      TEXT    NOT NULL,           -- JSON: the law's constructor arguments
    created_ns  INTEGER NOT NULL,           -- wall clock, ns since epoch
    session_id  INTEGER REFERENCES session(id) ON DELETE SET NULL,
    loop        TEXT,                       -- loop it was made for or on, if any
    notes       TEXT                        -- JSON: the plant it was fitted to, the rule, a comment
);
CREATE INDEX tuning_by_name ON tuning (name, created_ns DESC);
