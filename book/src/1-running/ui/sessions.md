# Sessions

**Sessions** (`#/sessions`): start and stop recording, the recorded sessions, and the daemon's rolling record. What a session holds and how it is exported over the API: [History](../../4-server/api.md#history); the daemon's retention keys: [The daemon section](../../2-config/daemon.md).

!!! tip "At the terminal"
    `flyball sessions`, `flyball export ID`, and the export routes by URL -- [Sessions and export](../cli/sessions.md). Start / stop, keep and pin are routes for now.

## Sessions

Rows carry a checkbox: click ticks one, shift-click ticks the range from the
last-clicked row, and ctrl/⌘-click ticks one without disturbing that anchor.
With one or more ticked, a bar offers **Delete** (through the same confirm
dialog as a single session, naming the count and the ids) and **Clear**; the
open session's row can't be ticked.

On a daemon that keeps a rolling record while nothing is being recorded
(`keep:` in its `daemon:` section — see [the daemon](../daemon/access.md)), the list
also shows that record as one row: "last 58 min held — not a recording",
with what the daemon trims it to and how much it holds. It is never a
session until you make one of it: **Keep…** takes the last 5, 15, 30 …
minutes (as much as is held) plus a name and notes and produces a closed
session like any other; and **Start recording** gains an **include the
last …** choice, so a recording started after something happened still
contains it. Charts seed their history from the rolling record exactly as
from a session, so an unrecorded rig shows its last hour on page load.

Where the daemon retains (`retain:`), each closed session's "ended" cell
says when it will be aged out, and a **pin** on the row keeps it past that;
a session the daemon continued at a rotation boundary (`rotate:`) carries a
`continues #n` chip back to the one before it. The section head states the
daemon's policy in one line — kept, retained, rotated, capped, and where
the store lives. The list may show more than one scratch row after a
restart: the earlier run's, closed, until it ages out of `keep`. What the
daemon does with all this and when: [The scratch record](../daemon/index.md#the-scratch-record).
