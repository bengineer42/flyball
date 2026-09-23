-- 0014: a signal's warning band is `warning`, not `warn`.
--
-- The rig-file key and the wire field became `warning` (a noun beside
-- `alarm`, as the severities are); the recorded declaration follows, so a
-- session's signal rows read back under the same name.

ALTER TABLE signal RENAME COLUMN warn TO warning;
