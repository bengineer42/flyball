# Storage

Recording and reading back. `Store` is the interface, `SqliteStore` the one
implementation.

## Two faces

A `SessionWriter` is bound to one open session and only appends: declare
sources, actuators and loops; write samples, ticks, events; open and close
spans; end. A `Store` opens sessions and reads any of them. The rig holds a
writer on its thread, the server the store on another, and either can be
replaced without the other noticing.

## What a session holds

| table | is |
| --- | --- |
| `session` | start, end, version, config, hardware, details |
| `source`, `measurand`, `channel` | what was declared |
| `sample`, `reading` | every reading, by channel |
| `actuator`, `loop` | what was driven, with its config |
| `tick` | one loop step: reading, setpoint, correction, demand, expected |
| `event` | something non-numeric that happened: a fault, a retune, a flag |
| `span` | a labelled interval, nestable by `parent_id`: program, run, command, note |
| `tuning` | named law configs, versioned; independent of sessions |

A run without its trace, the config that produced it, and the tuning in
force is not an experiment, so the session records all three.

## Times

Inside a session, times are `offset_ns` from the session's `start_ns`,
as integers. The wire carries the same integers; a client converts once and
nothing is rounded on the way out.

## Reading back

`series(session, source, measurand, window, downsample)` returns one channel
over a window. `Downsample` is one of three: `every` keeps every nth sample
(cheap, real readings, can alias); `bucket_ns` averages each bucket;
`max_points` averages into buckets sized to fit the window, and the store
reports the `bucket_ns` it resolved to so a client can label the axis.

`ticks`, `events` and `spans` read the same way. `tunings` are the newest
version of every name; `tuning_history` every version.

## Bluesky documents

`flyball.db.documents` walks a session and yields it as Bluesky event-model
documents — a `start`, one `descriptor` per source and per loop, an `event`
per sample or tick, a `stop` — the shape `bluesky.callbacks` and databroker
consume. `write_jsonl` saves them as JSON lines.

## SQLite

One locked connection per `SqliteStore`. The rig's writer and the server's
reader normally each open their own on the same file, and WAL lets them
overlap. Declarations are interned in the writer so the hot path — a
delivery — is one `executemany` per table with integer keys already known.

Migrations are numbered SQL files in `flyball/migrations`, each one
transaction; `schema_version` records the last applied, so opening an older
database brings it forward. `":memory:"` for tests.
