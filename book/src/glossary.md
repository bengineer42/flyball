# Glossary

**access** — which of readable (**R**), publishing (**P**) or writable
(**W**) a signal supports; the driver declares it, a rig file may only
restrict it -- unless the driver also names a **ceiling**, up to which the
rig file may widen it instead. `P` implies `R`.

**activity** — the ongoing part of a program step: a signal a program waits
on, that knows how to attach itself to the rig (a prompt, a settle test, a
timed hold).

**address** — a signal's or node's path: `device[.namespace…].signal`; no
dots inside a segment.

**bound input** — a signal on another device this one follows (`bound:` in
the rig file, an `Input` descriptor); when it lands the rig commits this
device, which reads it itself (`self.<input>.value`) in `commit` — there
is no callback.

**command** — a non-value action on a device: a method marked `@command`.
Also, a program step: a frozen dataclass with `run`, registered by tag.

**condition** — something true of a device *now*: offline, railed,
overdriven. In its state while it holds.

**config** — what a device (or a link, a law, a feedforward) was built
from; a pydantic model with `build()`. Changed only by rebuilding.

**correction** — what the law produces: the offset added to the
feedforward's demand.

**delivery** — one call to `rig.on_samples`: bound inputs mark devices
touched, then controllers tick, then one commit per device touched, then
the recorder.

**demand** — one or more values put on `W` signals under one node at one
instant; a sample in reverse.

**device** — a named thing in the rig with a tree of signals, commands and
conditions.

**driver** — which device code builds a device entry; the discriminator,
written `driver:` in the rig file (the Python side calls it the config
*tag*).

**event** — something that *happened*: a step failed, a device went
offline, a wait timed out. A point in time with a level, a scope and a
subject; streamed on `/ws/events` and written to the session when
recording.

**expected** — what a device says it will deliver a demand as; `None` if
it cannot say.

**feedforward** — what maps a controller's setpoint (source unit) to a
demand (target unit); the law's correction adds to it.

**handover** — entering regulation, or changing a tuning: choosing what the
correction should be at the instant of the switch (a `Transfer`).

**kind** — the discriminator for everything that is not a device: laws,
feedforwards, links, program steps.

**label** — a display name, from the rig file; `None` shows the address or
name instead.

**law** — a `ControlLaw`: `(elapsed, reading, setpoint) → correction`.

**link** — a transport devices talk over, or a simulated plant they share;
not a device, has no signals of its own.

**mode** — what a controller is doing: `manual`, `open` or `regulating`.

**namespace** — a node inside a device that groups signals; may be read as
one sample.

**node** — a device or a namespace inside one: the address, and everything
under it.

**overlay** — a rig file layered onto an earlier one: mappings deep-merge,
scalars and lists replace, `null` deletes. Swaps drivers behind the same
names — the real-vs-simulated pattern.

**output** — a signal that is produced, never set: a measurement, a
derived value, a mode (`Role.OUTPUT`, `RP`).

**plant** — the thing being controlled, as a model: gain, time constant,
dead time (simulation only).

**program** — an ordered list of commands. Data; no cursor.

**programmer** — runs a program against a rig, waiting where a step waits.

**quantity** — what is measured or set, independent of any device: a name
and a unit, nothing else.

**reading** — one value on one signal at one instant.

**reference** — where a controller is aiming: a value, or a generator.

**rig file** — a `.yaml`/`.toml`/`.json` file of links, devices and
controllers that `load_rig` builds.

**rig** — the clock, the devices, the controllers, the polling, the
recorder. What the equipment *is*.

**role** — what a signal is to its device: `Role.DEMAND` (settable),
`Role.OUTPUT` (produced), `Role.SETTING` (re-set by a command),
`Role.CONFIG` (effective at build) or `Role.INPUT` (bound, not in the
tree). Sets the signal's default access.

**sample** — every signal under one node at one instant, keyed by the
bound signal objects.

**session** — one recording: spans, samples, ticks, write states, events,
the config and tuning in force.

**setpoint** — the reference resolved at an instant, in the source's unit.

**setting** — a signal re-set by a command while a device runs, shown but
not driven by a controller (`Role.SETTING`, `RP`).

**signal** — one named value of one quantity on one device; has an address,
a role and an access set.

**structure tier** — the rig-level objects (`Node`, `Signal`, `Device`,
`Rig`, `Controller`): mutable, identity-hashed, made once at startup;
changed only through the rig, under its lock, as an event.

**tick** — one controller step on one reading.

**transfer** — how a handover seeds the correction: `none`, `reset`,
`carry`, `track`.

**tuning** — a named law config.

**controller** — one source signal, one law, one target signal, one
reference; named by the address of the signal it drives (a writable
signal has at most one).

**values tier** — the per-instant objects (`Reading`, `Sample`, `Demand`,
`WriteState`, `Event`): frozen; recorded, streamed, compared; never
changed after the fact.

**wait** — a signal fired once, and says how it ended: fired, timed out,
interrupted.

**write state** — what a device reports about one `W` signal after a
demand: value after limits, what was requested, whether it sits on a
limit, the controller driving it.
