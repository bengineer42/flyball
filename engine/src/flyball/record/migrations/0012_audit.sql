-- 0012: the runner's action audit.
--
-- One row per action on the rig: every request whose verb is not read, and every
-- stop, the break-glass signal included (`flyball.record.audit`). It is in wall time
-- (ns since the epoch), not the rig's clock, and it names no session: retention,
-- trimming and deleting a session never touch it. `boot` is one runner process and
-- `seq` counts its actions from 1, so a gap is an action the store never took (the
-- runner logged it instead). Rows are never changed or deleted: the triggers refuse.
--
-- `writes` is JSON, for a demand: {address: {old, requested, applied}}. `details` is
-- JSON too: a stop's reason. `status` is NULL for an action that was no request.

CREATE TABLE audit (
    id         INTEGER PRIMARY KEY,
    time_ns    INTEGER NOT NULL,
    boot       TEXT    NOT NULL,
    seq        INTEGER NOT NULL,
    principal  TEXT    NOT NULL,
    name       TEXT    NOT NULL DEFAULT '',
    sid        TEXT    NOT NULL,
    kind       TEXT    NOT NULL,
    via        TEXT    NOT NULL,
    cip        TEXT    NOT NULL,
    scheme     TEXT    NOT NULL DEFAULT '',
    method     TEXT    NOT NULL,
    route      TEXT    NOT NULL,
    path       TEXT    NOT NULL,
    status     INTEGER,
    outcome    TEXT    NOT NULL,
    request_id TEXT    NOT NULL DEFAULT '',
    writes     TEXT,
    details    TEXT,
    UNIQUE (boot, seq)
);

CREATE INDEX audit_by_time ON audit (time_ns);

CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit
BEGIN
    SELECT RAISE(ABORT, 'the audit is append-only: rows are never changed');
END;

CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit
BEGIN
    SELECT RAISE(ABORT, 'the audit is append-only: rows are never deleted');
END;
