# Storage

Recording and reading back. `Store` is the interface, `SqliteStore` the one
implementation.

## Two faces

A `SessionWriter` is bound to one open session and only appends: declare
devices, signals and controllers; write samples, ticks, write states,
events; open and close spans; end. A `Store` opens sessions and reads any of
them. The rig holds a writer on its thread, the server the store on
another, and either can be replaced without the other noticing.

## What a session holds

| table | is |
| --- | --- |
| `session` | start, end, flyball version, config, hardware, details |
| `device`, `signal` | what was declared: a device's driver and config, a signal's quantity, unit, access and bands |
| `write` | the signals whose writes this session records, with the driver behind them |
| `sample` | every reading, by signal, under one node's instant: `reading.value` and its `flag` (below) |
| `write_state` | what a writable signal was set to: one row per commit that touched it |
| `controller` | what was driven, named by its output's address, with its `measured` signal, law and feedforward |
| `tick` | one controller step: `measured` (the reading it stepped on), setpoint, correction, `output`, expected. `correction` is NULL when the law's output was not a number (a NaN integral), so the tick is kept rather than ending the recording. `reapplied` is 1 on a re-apply of a moving setpoint's feedforward between readings (no reading: `measured` NULL); one at the same instant as a reading's tick is not written |
| `event` | something non-numeric that happened: a fault, a retune, a flag |
| `span` | a labelled interval, nestable by `parent_id`: program, run, command, note |
| `tuning` | named law configs, versioned; independent of sessions |

Table and column names follow the device model: `device` is the address
of the node, `signal` the full address, `write`/`write_state` the signals
written and what they were set to, `controller` a control loop named by its
output.

A run without its trace, the config that produced it, and the tuning in
force is not an experiment, so the session records all three.

## Times

Inside a session, times are `offset_ns` from the session's `start_ns`,
as integers. The wire carries the same integers; a client converts once and
nothing is rounded on the way out.

A column's name says its clock (D-084): a bare `_ns` is the rig's clock
(a session's `start_ns`, every offset, a rig version's `time_ns`, a saved
tuning's, program's or dashboard's `created_ns`), and a `_utc_ns` is wall-clock
time (the audit's `time_utc_ns`, a latch's `at_utc_ns`, a live value's
`written_utc_ns`). They read the same on hardware; a simulated rig's clock
runs scaled or stepped, and there they differ.

## Reading back

`series(session, address, window, downsample)` returns one signal over a
window, by its full address (`"hum_sensors.dry.humidity"`). `Downsample` is
one of three: `every` keeps every nth sample (cheap, real readings, can
alias); `bucket_ns` averages each bucket; `max_points` averages into buckets
sized to fit the window, and the store reports the `bucket_ns` it resolved
to so a client can label the axis.

`ticks(session, controller, ...)`, `events` and `spans` read the same way.
`tunings` are the newest version of every name; `tuning_history` every
version.

## Recording a session

`flyball.runtime.Recorder(writer, signals, controllers, flush_s=0.1, on_failure=None)`
is not an observer: it wants the whole delivery, after the controllers have
ticked and the touched devices have committed, so it records what each tick
produced and what each commit set. The rig holds at most one and calls
`recorder.record(samples, ticks, states, time_ns=...)` at the end of every
delivery ([on_samples][flyball.rig.rig.Rig.on_samples]), and again with
empty samples and ticks after a manual demand made outside one; a blocking
device's deferred write states reach it the same way, through
`Rig.written`, once its writer thread finishes the commit.

What is recorded follows a signal's access: readings of published (`P`)
signals go in as samples — a fresh read of an `RW` setting is for whoever
asked for it, not the record — and write states of writable (`W`) ones go
in as `write_state` rows; a controller's measured signal and output are always
included, asked for or not. Declaring (`declare_device`, `declare_signal`,
`declare_controller`) is idempotent and happens once, at construction, over
the signals and controllers given — which the rig defaults to every signal
that publishes or is written, on every device, and every controller.

Deliveries are buffered on the delivery path — appends to four lists under
one lock — and written by the recorder's own thread in one transaction
every `flush_s`: a transaction costs milliseconds on an SD card whether it
holds one row or a hundred, and none of those milliseconds are the
delivery's. A store that fails ends the recording (`on_failure` is called
once; the rig turns it into a `recording_failed` event) without touching
control. `close` stops the thread, writes what is left, and ends the
session.

The recorder is a write-behind log of what happened: nothing on the
delivery or control path ever reads it back, and a controller's law never
does either — reading it back is what [Reading back](#reading-back) above
is for.

## Bluesky documents

`flyball.record.documents` walks a session and yields it as Bluesky event-model
documents — a `start`, one `descriptor` per device and per controller, an
`event` per sample or tick, a `stop` — the shape `bluesky.callbacks` and
databroker consume. `write_jsonl` saves them as JSON lines.

## SQLite

One connection per `SqliteStore`, and one `RLock` around it: every query
and every transaction holds the lock for its whole length. `flyball-runner`
opens one store and shares it — the recorder's thread writes through it, the
server reads through it, the retention sweep deletes through it — so they
take turns at that lock. Another process (a copy being read, `sqlite3` at a
shell) can open the same file beside it, and WAL lets them overlap.
Declarations are interned in the writer so the hot path — a delivery — is one
`executemany` per table with integer keys already known.

Whoever waits for the lock waits as long as the holder takes, so nothing on
the server's event loop calls the store. A route that takes `StoreDep` is a
plain `def`, which FastAPI runs on its threadpool; one that must stay `async`
(reading a request body) hands the store call to `anyio.to_thread`. An
`async` route that called the store would, while another thread held the
lock, freeze every request and websocket the runner serves. `StoreDep` also
takes one of four `STORE_SLOTS` for the request, so a pile of history reads
queued at the lock waits on the loop rather than filling the 40 worker
threads that every other sync route (demands, commands) shares. The test
suite fails any test in which the app's loop took the store's lock. Nor does
a delete or a trim hold the lock for long, however large the session:
[Deleting a session](#deleting-a-session) below.

The schema is numbered SQL files in `flyball/record/migrations`, each one
transaction, applied in order when a store is opened; `schema_version`
records the last applied. There is one: `0001_initial.sql`, the baseline,
which makes every table, index and trigger below on an empty file and stamps
the file's `PRAGMA application_id` with `0x666C7962` ("flyb",
`flyball.record.migrate.APPLICATION_ID`). The chain that came before it was
folded into it at the R1 rename (D-064: no store then held anything worth
keeping), and nothing converts a store it made. A file with tables but not
the stamp is refused with a `SchemaError` before anything is read: one with a
`schema_version` table ("this store was made by a pre-reset flyball (before
the R1 rename); delete it: nothing in it needs keeping (D-064)"), or any
other ("not a flyball store"). A store at a version newer than any this
flyball ships is refused too, not opened and misread: a newer flyball wrote
it. `flyball-runner` exits 2 with the message, so its supervisor does not
start it again. `":memory:"` for tests.

Every statement goes through one of two helpers, `_query` (reads) and
`_transaction` (writes), and they classify what sqlite raises: an
`IntegrityError` becomes `ConstraintError` (a `ConflictError`, 409), an
`OperationalError` `StoreUnavailableError` (a `HardwareError`, 503), with
sqlite's error kept as `__cause__`. Anything else -- a `ProgrammingError`
from a closed store -- passes through unchanged, because it is a bug and an
honest 500 beats an outage nobody can wait out. `OperationalError` also
covers a transaction begun inside another, which is a bug too; sqlite's
message in `detail` tells the two apart. A failed `ROLLBACK` is swallowed so
it cannot replace the error that caused it. `used_bytes` reads the page
counts directly and raises raw; its only caller, retention's sweep, catches
everything.

The rig's history is `rig_version`: one row per version, the whole document
each time (never a diff, so any row stands alone), `parent_id` the version
it was made from, and a one-row `rig_head` naming where the running rig
is. Saving a version chains it to the head and moves the head to it. A
rig edit (D-051) saves one (`edited: ...`) before the runner restarts on
it, and a restore saves the restored document again as a new version
(`restored from N`) on top of the head; a start writes one only when the
rig it built differs from the head (`loaded`, `started bare`, `resumed`),
and not at all when it is the start an edit asked for. `--resume` walks back
from the head past start rows to the last edit or restore. A session's
`config` is the record of what it built and is never loaded again.

An event's `code` names what happened, and its JSON `details` carry the
`severity` as the lowercase string the wire uses (`debug`, `info`,
`warning`, `error`). `event.edge` is `raised` (a condition began), `cleared`
(it ended) or NULL for a point event (a program step, a restore); the edge is
a column, so a session's condition history is one indexed query.

`reading.value` may be NULL (D-048, A2): a reading with no value is kept, as
NULL with the code of its quality in `reading.flag`, and a value may carry a
mark. The codes never meet on one row:

| `flag` | with | means |
| --- | --- | --- |
| NULL | a value | a plain reading |
| 1 | NULL | `invalid` |
| 2 | NULL | `not_applicable` |
| 3 | NULL | `stale`, any reason but the next |
| 4 | NULL | `stale`: the device was offline |
| 5-15 | NULL | free |
| 16, 17 | a value | `at_limit`: low, high |
| 18-31 | a value | free |

Two CHECKs hold it: a NULL value has a code in 1-15, a value has none or
one in 16-31, and the flag is an integer. The writer never relies on them
-- a NaN or an infinity that reached it is stored NULL with code 1, not
refused -- so a CHECK failing is a bug, and a `ConstraintError` that ends
the batch. `pending` writes no row.
`reading_by_signal` is `(session_id, signal_id, offset_ns, value, flag)`, so
a series with its breaks and marks is still one range scan. Reading back,
`series` gives each point its `flag`; `every=n` keeps every NULL row
besides every nth, so thinning never hides a break; an averaged bucket
with any NULL in it is NULL, with the lowest code in it; `samples` gives
each row its `flags`. Which stale reason (other than an offline device)
is not kept; the device's `write_failed` edges say when its writes failed.

`tick.reapplied` (`INTEGER NOT NULL DEFAULT 0`, 0 or 1) marks the ticks a
controller records when it re-applies a moving setpoint between readings.

`live_value` (C10(5)) holds one row per signal whose last
written value a restart restores, keyed `(device, signal)`, with `kind`
(`value` for a `driver: values` entry; `setting`, for a driver setting
behind a config field, is reserved for live settings, C11), `value` and
`initial` as JSON (a later setting may be a string, a bool or an enum
member), `unit` (the symbol when it was written), `config_field` (a
setting's; NULL for a value), `actor` (who wrote it, JSON `{principal, kind, via, sid, message}`),
`written_utc_ns` (wall time) and `head_version` (the rig version in force). It
does not depend on recording: the runner always opens the store. At start,
`Rig.values.attach` restores a row while the rig file's `initial` still
equals the row's and the unit is the same; a changed `initial` means the
file was edited since, and the row is deleted; a changed unit leaves the
file's `initial` in force and raises `value_not_restored` on the signal.
`put_live_value` refuses a secret (`SecretStr`, `SecretBytes`).

`latch` holds one row per latch cause held -- the rig
stop (`stop`) or a controller's `on_fault` action (`on_fault:<controller>`)
-- with `subjects` as JSON `[{subject_kind, subject}]` (`rig`, `device`, `signal`,
`controller`), `actor` (who set it, JSON `{principal, kind, via, sid, message}`: a stop's person or
agent, a fault's controller), `at_utc_ns` (wall
time), `reason` and `action` (a fault's). A row is written when the latch
is set and deleted by its Reset. At start, before serving,
`Stopping.attach` restores every row and re-applies the stop it implies: a
rig stop's stops every device again, a fault's `stop` or `stop_device`
stops what it held ([the latch](../1-running/runner/access.md#the-latch)).

The scratch record and retention (D-008) are `session.kind`,
`origin_ns`, `pinned`, `continues`, `bytes`. Trimming a scratch session
deletes rows and moves `start_ns` without rewriting offsets: they stay
relative to the hidden `origin_ns`, and every read shifts by
`start_ns - origin_ns` (`SqliteStore._shift`). A session's `bytes` is rows ×
a measured bytes-per-row (`_ROW_BYTES`); the store's `used_bytes()` is pages
less free pages × page size, which is what `max_store` is measured against.
Samples backfilled into a new recording (`include_ns`) are numbered from −1
downwards, below the writer's own count, so the recorder need not know. The
sweep itself is `flyball.runtime.retention.Retention`, started by `serve()`
when there is a store; what it does and in what order is
[What ages out](../1-running/runner/index.md#what-ages-out).

The runner's action audit is the `audit` table, one row per
action on the rig -- every request whose verb is not read, and every stop,
the `SIGUSR1` break-glass included. A row is its actor, the verified principal
(`principal`, `name`, `sid`, `kind`, `via`), its `cip`, and `scheme` (how it got in), the
`method`, the `route` (its template) and `path`, the `status` and its
`outcome` (`done`, `denied` by the door, `refused` by the rig, `failed`), the
`request_id` (the front's `X-Request-Id`, or one the runner makes), and as
JSON a demand's `writes` (`{address: {old, requested, applied}}`) and a
stop's `details` (its reason). It is in wall time (`time_utc_ns`), not the rig's
clock; it names no session, so retention and deleting a session never reach
it; and triggers refuse any `UPDATE` or `DELETE` on it. `boot` is one runner
process and `seq` counts its actions from 1.

`flyball.record.audit.Auditor` writes the rows on a thread of its own, so
neither the event loop nor the break-glass's thread waits for the store. If
a write fails -- the disk full, the store locked -- the action has still
happened: the failure is logged, with the action in full as one JSON line,
and nothing is refused; the missing `seq` shows the gap. The same when more
than 10 000 actions are waiting. The middleware is
`flyball.interfaces.server.audit.Audit`, outside the door so it sees who was
refused: a request with no principal, a bad one or an anonymous one (the
`anonymous` scheme, or the `anon:` subject the front gives a visitor with no
credential) identifies no one and is not recorded, nor is a CORS preflight.
An identified caller's `denied` rows are kept to `DENIED_PER_MINUTE` (10) a
minute per `principal`, and a denied demand's `writes` to its first
`DENIED_WRITES` (16) addresses; the log counts what was left out. The table
is never trimmed, so a caller refused over and over adds little to it.

### Deleting a session

A week of 1 Hz samples took 8 s to delete in one transaction, all of it with
the store's lock held. `delete_session` goes in pieces instead:

1. one transaction marks the row: `details.deleting: true`;
2. the data — samples (their readings by the cascade), ticks, write states,
   events — goes a batch at a time, `_DELETE_ROWS` (1000) readings or rows
   per transaction, the lock released between batches;
3. one last transaction deletes the row, and its declarations and spans by
   the cascade.

That same week is now 610 transactions of at most ~35 ms each (desktop SSD).
`trim_session` deletes the rows before its cut the same way, then moves
`start_ns`.

What is traded is atomicity. A reader between batches sees the session
partly gone, and a runner killed mid-delete leaves the row marked and
part-deleted. That is visible and recoverable, not silent: the row is still
listed, `details.deleting` says why it is short, `deleting_sessions()` lists
every such row, and deleting it again finishes the job — `flyball-runner`
does that for each one when it opens the store, before anything reads it. A
trim cut off part-way leaves `start_ns` where it was, over a few missing
rows at the start, and the next sweep's trim finishes it.

Still whole transactions, and so still as long as their data is large:
copying a range (`keep_range`, `backfill` — 0.8 s for a day, 4.6 s for a
week here) and reads of a long session without a window (`samples` 1.6 s,
`series` 0.4 s, for a week). None of them runs on the event loop, so they
delay only other store calls: the recorder's flush, which buffers; retention;
and starting or stopping a recording, which calls the store under the rig's
lock, so deliveries wait for as long as it does.
