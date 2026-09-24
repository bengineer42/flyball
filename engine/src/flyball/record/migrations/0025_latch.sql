-- 0025: latches that outlive the process (the software stop's latch, D-048..D-052 stage A).
--
-- One row per cause held: the rig stop (`stop`) or a controller's `on_fault`
-- action (`on_fault:<controller>`), with the subjects it holds as JSON
-- `[{scope, subject}]` (`rig`, `device`, `signal`, `controller`). A row is written
-- when the latch is set and deleted by its Reset; a runner started on this store
-- restores every row, and a rig stop's row re-applies the stop before serving.
-- `by` is who set it (the principal's `sub`, or `on_fault`), `at_ns` when (wall
-- time, ns since the epoch), `reason` what they said, `action` a fault's action.
CREATE TABLE latch (
    cause     TEXT    PRIMARY KEY,
    subjects  TEXT    NOT NULL,              -- JSON [{scope, subject}]
    by        TEXT    NOT NULL,
    at_ns     INTEGER NOT NULL,
    reason    TEXT    NOT NULL DEFAULT '',
    action    TEXT    NOT NULL DEFAULT ''
);
