-- The rig as a versioned document: what the daemon loaded, then every change
-- made to the running rig (a link or device added or removed, a controller
-- attached or detached, a simulation's knobs saved). Each row is the whole
-- rendered rig file, never a diff, so any version stands alone; a delta is
-- computed on read. A session records the version it started on.
CREATE TABLE rig_version (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    time_ns     INTEGER NOT NULL,
    reason      TEXT    NOT NULL,           -- "loaded", "added device blender", ...
    files       TEXT,                       -- JSON: the files the daemon loaded, for provenance
    document    TEXT    NOT NULL            -- JSON: the rig file as it stood
);

ALTER TABLE session ADD COLUMN rig_version_id INTEGER REFERENCES rig_version(id);
