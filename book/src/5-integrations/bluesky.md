# Bluesky

**What.** Two directions. In-process, any flyball node or writable signal
can stand in a Bluesky plan: `NodeReadable` (a device or namespace as a
readable) and `SignalMovable` (a writable signal as a movable), in
`flyball_bluesky` (`pip install flyball-bluesky`). Out of the store, a
recorded session exports as **event-model documents** -- what databroker,
tiled and the facility analysis tools read.

**What comes through.**
 A session becomes a `start`, a
`descriptor` per stream (one per device, one per controller, with each key's
units), an `event` per sample or tick, and a `stop`.

```
flyball sessions
flyball export 12 --out run12.jsonl        # {"name": ..., "doc": ...} per line
GET /api/history/sessions/12/documents     # the same, as [[name, doc], ...]
```

In Python, `flyball.db.documents.documents(store, session_id)` yields the
`(name, doc)` pairs a Bluesky callback or `databroker.v2` consumes directly.

**Configure.** Nothing: the readables wrap a running rig's objects; the
export is a route ([History](../4-server/api.md#history)) and a CLI command
([Sessions and export](../1-running/cli/sessions.md)).

**Code.** `flyball_bluesky` (`extensions/bluesky`), `flyball.db.documents`.
