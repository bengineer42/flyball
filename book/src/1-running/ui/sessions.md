# Sessions

**Sessions** (`#/sessions`): start and stop recording, the recorded sessions, and the runner's rolling buffer(s). What a session holds and how it is exported over the API: [History](../../4-server/api.md#history); the runner's retention keys: [The runner section](../../2-config/runner.md).

!!! tip "At the terminal"
    `flyball sessions`, `flyball export ID`, and the export routes by URL -- [Sessions and export](../cli/sessions.md). Start / stop, keep and pin are routes for now.

## Buffers

On a runner that keeps a rolling record while nothing is being recorded
(`keep:` in its `runner:` section — see [the runner](../runner/access.md)),
a separate **Buffers** table sits above Sessions — a buffer isn't a session
(no pin, no end, no download/delete the same way), so it gets its own
columns rather than being squeezed into the sessions table's shape. The
still-rolling one, if any, is highlighted first with a **current** chip; a
runner that just restarted may show older ones below it too — buffers that
already stopped and haven't aged out of `keep` yet — newest first, their
own stop time in the status column instead of "current".

A buffer is never a session until you make one of it: **Keep…** takes the
last 5, 15, 30 … minutes (as much as it holds) plus a name and notes and
produces a closed session like any other, keyed to the buffer's own end (or
now, for the still-rolling one) — not the wall clock, so it's correct
against a simulated rig running at its own speed. Keeping from an
already-stopped buffer also forgets it afterwards: its data now lives in
the new session, so the source would just be a second, orphaned copy of the
same data otherwise. An older buffer also has its own **Forget** button, to
discard it without keeping from it first. **Start recording** gains an
**include the last …** choice, so a recording started after something
happened still contains it. Charts seed their history from the current
buffer exactly as from a session, so an unrecorded rig shows its last hour
on page load. The section head names how long history is kept, without the
store's file path.

## Sessions

Rows carry a checkbox: click ticks one, shift-click ticks the range from the
last-clicked row, and ctrl/⌘-click ticks one without disturbing that anchor.
With one or more ticked, a bar offers **Delete** (through the same confirm
dialog as a single session, naming the count and the ids) and **Clear**; the
open session's row can't be ticked.

Where the runner retains (`retain:`), each closed session's "ended" cell
says when it will be aged out, and a **pin** on the row keeps it past that;
a session the runner continued at a rotation boundary (`rotate:`) carries a
`continues #n` chip back to the one before it. The section head states the
runner's retention policy in one line — kept, rotated, capped. Download and
delete are buttons, not bare icons, next to the pin toggle. What the runner
does with buffers and when:
[The scratch record](../runner/index.md#the-scratch-record).

## Renaming a session

Opening a session shows its name as a text box, not a heading: type a new
one and a **save name** button appears (blue, disabled until there's
something to save) — the box starts blank with "session name" as
placeholder text, never pre-filled with anything that would get saved just
by clicking without typing.

Opening a buffer instead of a session shows the same box, but it says
**save session**: saving there does more than rename, it keeps the buffer
as a real session under that name (the same as **Keep…** from the Buffers
table, whether the buffer is still rolling or already stopped). A session
is never required to have a name; the sessions list shows "—" for one that
doesn't.
