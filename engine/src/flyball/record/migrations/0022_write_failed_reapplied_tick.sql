-- 0022: one code for a failed write, and a tick with no reading (signal faults, wave 2 stage 5).
--
-- A6 unifies the two write-failure codes: a commit on the delivery path that
-- raised was `commit_failed`, a blocking device's writer that failed was
-- `write_failed`. Both are `write_failed` now, so what was stored as
-- `commit_failed` (both edges) is renamed.
--
-- E25: a controller following a moving setpoint re-applies its feedforward
-- between readings, on the rig clock. That tick has no reading: `reapplied`
-- is 1 on it, and its `measured` is NULL. Every tick stored before is a
-- reading's (0).

UPDATE event SET code = 'write_failed' WHERE code = 'commit_failed';

ALTER TABLE tick ADD COLUMN reapplied INTEGER NOT NULL DEFAULT 0 CHECK (reapplied IN (0, 1));
