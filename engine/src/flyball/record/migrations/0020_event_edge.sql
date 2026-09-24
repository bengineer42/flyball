-- 0020: a condition's start and end are events told apart by `edge` (C15, signal faults).
--
-- `edge` is `raised` (the condition began), `cleared` (it ended) or NULL (a
-- point event: a program step, a restore). What the runtime raised as a
-- condition before this store had edges is marked `raised`; the codes that
-- said a condition had ended become the `cleared` edge of their pair:
--
--   write_recovered  -> write_failed  cleared
--   commit_recovered -> commit_failed cleared
--   step_recovered   -> step_failed   cleared (a controller's; a program's step_failed is a point)
--   limit_known      -> limit_unknown cleared
--   restarted        -> offline       cleared (a device's polling; `restarted` is the runner's now)
--
-- `slow` had no end before: every slow read was its own event, so each
-- stays a lone `raised` with no `cleared` after it.

ALTER TABLE event ADD COLUMN edge TEXT;

UPDATE event
SET edge = 'raised'
WHERE code IN (
    'offline', 'slow', 'write_failed', 'commit_failed', 'stale_input', 'limit_unknown',
    'recording_failed'
)
OR (code = 'step_failed' AND json_valid(details) AND json_extract(details, '$.subject_kind') = 'controller');

UPDATE event
SET edge = 'cleared',
    code = CASE code
        WHEN 'write_recovered' THEN 'write_failed'
        WHEN 'commit_recovered' THEN 'commit_failed'
        WHEN 'step_recovered' THEN 'step_failed'
        WHEN 'limit_known' THEN 'limit_unknown'
        WHEN 'restarted' THEN 'offline'
    END
WHERE code IN ('write_recovered', 'commit_recovered', 'step_recovered', 'limit_known')
OR (code = 'restarted' AND json_valid(details) AND json_extract(details, '$.subject_kind') = 'device');
