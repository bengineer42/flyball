-- 0003: stored programs.
--
-- A program is kept as the text it was written in -- YAML, TOML or JSON --
-- byte for byte, so comments and the author's layout survive. Every save
-- under a name adds a row; readers want the newest per name, and the history
-- is the edit trail. Nothing here is parsed: the dialect does that on read,
-- and a program that no longer validates is still a document worth keeping.

CREATE TABLE program (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,           -- "dry-then-hold"
    format      TEXT    NOT NULL,           -- "yaml", "toml" or "json": what `body` is written in
    body        TEXT    NOT NULL,           -- the document, verbatim
    created_ns  INTEGER NOT NULL,           -- wall clock, ns since epoch
    notes       TEXT,                       -- JSON: what the author said about this version
    sha256      TEXT    NOT NULL            -- of body; identical re-saves are cheap to spot
);
CREATE INDEX program_by_name ON program (name, created_ns DESC);
