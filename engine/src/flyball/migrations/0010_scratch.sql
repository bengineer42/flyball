-- 0010: the scratch record, pins, continuations.
--
-- A session now has a *kind*: "session" is a recording someone started;
-- "scratch" is the rolling record the runner keeps while nothing is being
-- recorded, trimmed to the last `keep` of the rig's clock. Trimming deletes
-- a scratch session's oldest rows and moves its `start_ns` forward to the
-- oldest row kept, so the row says what it holds. Offsets are not rewritten
-- when that happens: they stay relative to `origin_ns`, the session's start
-- as it was opened, and a read shifts them by `start_ns - origin_ns`. For a
-- session that was never trimmed the two are equal.
--
-- `pinned` exempts a session from retention; `continues` names the session a
-- rotated one carries on from; `bytes` is the runner's latest estimate of
-- what a scratch session holds on disk, for the sessions list.

ALTER TABLE session ADD COLUMN kind TEXT NOT NULL DEFAULT 'session';
ALTER TABLE session ADD COLUMN origin_ns INTEGER;
ALTER TABLE session ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0;
ALTER TABLE session ADD COLUMN continues INTEGER REFERENCES session(id) ON DELETE SET NULL;
ALTER TABLE session ADD COLUMN bytes INTEGER;

UPDATE session SET origin_ns = start_ns;

CREATE INDEX session_by_kind ON session (kind, start_ns);
