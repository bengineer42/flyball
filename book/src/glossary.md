# Glossary

**access** — which of readable (**R**), published (**P**) or writable
(**W**) a signal supports; the driver declares it, a rig file may only
restrict it -- unless the driver also names a **ceiling**, up to which the
rig file may widen it instead. `P` implies `R`.

**activity** — the ongoing part of a program step: a signal a program waits
on, that knows how to attach itself to the rig (a **prompt**, a **settle**
test, a timed **wait**, a ramp's end).

**address** — a signal's or node's path: `device[.namespace…].signal`; no
dots inside a segment.

**adoption** — a starting `flyballd` taking over a runner a previous
`flyballd` left running: it finds the runner in its front-dir, checks it
with the signed handshake and routes to it again, with no restart
(D-037).

**cancelled** — how a program ends when a person ends it: the cancel
button, `POST /api/programs/cancel`, a new program started with `cancel`,
or cancelling what it waits on. Outputs are kept. Compare **interrupted**.

**command** — a non-value action on a device: a method marked `@command`,
run by `POST /api/devices/{name}/commands/{command}`. A program's steps are
**steps**, not commands.

**condition** — something true of a device *now*: offline, railed,
overdriven. In its state while it holds.

**config** — what a device (or a link, a law, a feedforward) was built
from; a pydantic model with `build()`. Changed only by rebuilding. In a
rig file a device's config is its driver's fields, flat beside the
envelope.

**controller** — a software control loop, not a device: one measured
signal, one law, one output (a demand), one setpoint; named by the
address of its output (a demand has at most one). Its law, tuning and
feedforward in force are its `ControllerSpec`.

**correction** — what the law produces: the offset added to the
feedforward's output value.

**delivery** — one call to `rig.on_samples`: bound inputs mark devices
touched, then controllers tick, then one commit per device touched, then
the recorder.

**demand** — a signal whose writing takes control of the process
(`Role.DEMAND`): it has one owner at a time, is the only thing a controller
may drive, and is what a stop acts on. A value put on it is a **write**.

**device** — a named thing in the rig with a tree of signals, commands and
conditions.

**driver** — which device code builds a device entry; the discriminator,
written `driver:` in the rig file (the Python side calls it the config's
`type_name`).

**dwell** — the setpoint generator's segment that holds a value for a
duration (`{type: dwell, value, duration}`), used in a profile's `segments`
and in `POST /api/controllers/{c}/setpoint`'s `at`; the generator's
equivalent of the program step **wait**.

**event** — something that *happened*: a step failed, a device went
offline, an activity timed out. A point in time with a level, a scope and a
subject; streamed on `/ws/events` and written to the session when
recording.

**expected** — what a device says it will deliver a demand as; `None` if
it cannot say.

**feedforward** — what maps a controller's setpoint (measured unit) to an
output value (the output's unit); the law's correction adds to it.
`identity` passes the setpoint straight through, `none` gives 0, `affine`
and `table` model the plant.

**front** — the Go server in front of every runner that `flyball run`
and `flyballd` start: it serves the dashboard, decides who a caller is
(its shape), checks `Host` and `Origin`, terminates TLS, and passes
`/api`, `/ws` and `/mcp` to the runner with a signed principal.

**front-dir** — the private directory (mode 0700) a front makes for each
runner it starts: the principal's key, the runner's audience and
endpoint, its socket and its `runner.lock`.

**inputs** — what a device follows on another device, by the input's name:
`inputs: {dry: hum_sensors.dry.humidity}` in the rig file, an `Input`
descriptor in the driver. When the source lands the rig commits the device,
which reads it itself (`self.<input>.value`) in `commit` — there is no
callback.

**interrupted** — how a program ends when the engine ends it (a software
stop, a shutdown), with the reason; outputs are kept. A controller put in
manual by a device command is interrupted too. Compare **cancelled**.

**handover** — entering regulation, or changing a tuning: choosing what the
correction should be at the instant of the switch (a `Transfer`).

**label** — a display name, from the rig file; `None` shows the address or
name instead.

**law** — a `ControlLaw`: `(elapsed, measured, setpoint) → correction`.

**link** — a transport devices talk over, or a simulated plant they share;
not a device, has no signals of its own.

**mode** — who drives a controller's output: `manual` (a person) or
`regulating` (the law). Open loop is a law (`open_loop`), and frozen a
condition, not modes.

**namespace** — a node inside a device that groups signals; may be read as
one sample.

**node** — a device or a namespace inside one: the address, and everything
under it.

**overlay** — a rig file layered onto an earlier one: mappings deep-merge,
scalars and lists replace, `null` deletes. Swaps drivers behind the same
names — the real-vs-simulated pattern.

**measured** — a controller's measured signal: the published signal it
regulates (ISA's PV), `measured:` in the rig file. Also the faceplate row
and the wire field holding its last reading.

**output** — a controller's output: the demand it writes (ISA's OP); the
controller is named by its address. Also the faceplate row and the wire
field holding the last value asked of it. Not a role: a signal a device
produces is a **readout**.

**plant** — the thing being controlled, as a model: gain, time constant,
dead time (simulation only).

**principal** — who a request is for, as the front signs it for one
runner: the caller's id (`sub`), session, verbs on that rig (`scp`), kind
and audience, valid for 60 s, in the `X-Flyball-Principal` header.

**program** — an ordered list of steps. Data; no cursor. It ends
`succeeded`, `failed`, **cancelled** or **interrupted**.

**programmer** — runs a program against a rig, waiting where a step waits.

**prompt** — a program step: pause until an operator fires it, or it times
out. Registers as an **activity** under its `name`, `prompt` by default. Not
the same thing as an MCP prompt.

**quantity** — what is measured or set, independent of any device: a name
and a unit, nothing else.

**readback** — a demand's reported current value: what the device says it
holds, beside what was last written. Not a role; compare **readout**.

**reading** — one value on one signal at one instant.

**readout** — a signal the device produces and nothing outside writes: a
measurement, a derived value, a mode (`Role.READOUT`, `RP`, the `Readout`
descriptor). A role; not the same thing as a demand's **readback**.

**reference** — inside the law's maths, where a controller is aiming: a
value or a setpoint generator. Everywhere a person sees it, it is the
**setpoint**.

**rig file** — a `.yaml`/`.toml`/`.json` file of links, devices and
controllers that `load_rig` builds.

**rig** — the clock, the devices, the controllers, the polling, the
recorder. What the equipment *is*.

**role** — what a signal is to its device, by what writing it does:
`Role.DEMAND` (settable; takes control of the process; the only thing a
controller drives), `Role.READOUT` (produced, never written from outside),
`Role.SETTING` (re-set by a command; changes how the device behaves) or
`Role.CONFIG` (set at build). Sets the signal's default access. An
input is not a role: it is a binding to another device's signal.

**sample** — every signal under one node at one instant, keyed by the
bound signal objects.

**session** — one recording: spans, samples, ticks, write states, events,
the config and tuning in force.

**setpoint** — the reference resolved at an instant, in the measured
signal's unit; the faceplate's middle row (ISA's SP).

**setting** — a signal re-set by a command while a device runs, shown but
not driven by a controller (`Role.SETTING`, `RP`).

**settle** — a program step: wait until the named controllers sit within a
band of their setpoints for `count` consecutive readings, or time out.
Registers as an **activity** named `settle:<controllers>`.

**signal metadata** — a signal's descriptive and limiting fields (label,
range, precision, warning, alarm, limits, poll_s, stale_after_s, max_rate,
tags), from the driver and then the rig file's `signals:` (`SignalMeta`,
`NamespaceMeta`); applied in place, without a rebuild.

**shape** — which door a front has, set by `auth:`: `local` (no sign-in,
loopback only), `password` (an admin password and named tokens) or
`proxy` (an identity proxy in front).

**signal** — one named value of one quantity on one device; has an address,
a role and an access set.

**software stop** — interrupting any running program and putting every
controller in manual, for everyone at once (`POST /api/rig/stop`, `flyball
stop`, the **Software stop** button, `SIGUSR1`). A control function, not
an emergency stop; in this release it writes nothing to any device.

**step** — one entry of a program, keyed by its name: `regulate`, `ramp`,
`wait`, `settle`, `manual`, `set`, `command`, `prompt` (the Python base
class is `Step`). Also, loosely, one move of a stepped clock, which is
**advanced**.

**tags** — labelled axes that group signals across the tree, never part of
an address: `line: dry` on `flows.dry` and `efforts.dry`.

**tick** — one controller update on one reading.

**transfer** — how a handover seeds the correction: `none`, `cold`,
`carry`, `track`.

**tuning** — a named law config.

**type** — the discriminator, written `type:`: which implementation a link,
a law, a feedforward or a setpoint generator entry is (`type: PI`,
`type: sim_plant`). A device names its implementation with `driver:`
instead; a program step has no discriminator (its key is the step).

**verb** — what a caller may do on a rig, and what each route needs:
`read` or `operate` for now (the vocabulary is pending D-034). A
**scope** grants a verb on one rig (`operate:furnace`) or every rig
(`operate`).

**wait** — a program step: keep everything as it is for a `duration`,
controllers going on regulating throughout, or time out. Registers as an
**activity** named `wait`. Not to be confused with **activity**, the general
thing a program step waits on (which also covers a **prompt**, a
**settle** test, or a ramp's end).

**write** — one or more values put on `W` signals under one node at one
instant (`Write`, `Rig.write`); a sample in reverse.

**write state** — what a device reports about one `W` signal after a
write: value after limits, what was requested, whether it sits on a
limit, the controller driving it.
