-- Versions as a tree with a head, not a log with copies. A change made to
-- the running rig is a new row whose parent is the head; restoring a version
-- moves the head to it and writes nothing, so a version restored and then
-- changed branches from the one restored. The rows already here were a
-- line, so each is chained to the one before it and the head is the last.
ALTER TABLE rig_version ADD COLUMN parent_id INTEGER REFERENCES rig_version(id);

UPDATE rig_version
SET parent_id = (SELECT MAX(v.id) FROM rig_version v WHERE v.id < rig_version.id);

CREATE TABLE rig_head (
    one        INTEGER PRIMARY KEY CHECK (one = 1),   -- a single row
    version_id INTEGER NOT NULL REFERENCES rig_version(id)
);

INSERT INTO rig_head (one, version_id)
SELECT 1, MAX(id) FROM rig_version HAVING MAX(id) IS NOT NULL;
