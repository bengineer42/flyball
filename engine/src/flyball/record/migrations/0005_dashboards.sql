-- Dashboards: what the UI shows and how, saved per rig. Append-only like
-- tunings and programs: a save adds a version; the newest under a name wins.
CREATE TABLE dashboard (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,           -- "furnace overview"
    rig         TEXT    NOT NULL,           -- the rig it was made for, by name
    body        TEXT    NOT NULL,           -- JSON: the dashboard document
    created_ns  INTEGER NOT NULL,           -- wall clock, ns since epoch
    sha256      TEXT    NOT NULL            -- of body; identical re-saves are cheap to spot
);
CREATE INDEX dashboard_by_name ON dashboard (name, created_ns DESC);
CREATE INDEX dashboard_by_rig ON dashboard (rig, name);
