# Glossary

**access** — which of readable (**R**), published (**P**) or writable
(**W**) a signal supports; the driver declares it, a rig file may only
restrict it -- unless the driver also names a **ceiling**, up to which the
rig file may widen it instead. `P` implies `R`.

**activity** — the ongoing part of a program step: a signal a program waits
on, that knows how to attach itself to the rig (a **prompt**, a **settle**
test, a timed **wait**, a ramp's end).

**address** — a signal's or node's path: `device[.namespace…].signal`, each
segment a **key**.

**adoption** — a starting `flyballd` taking over a runner a previous
`flyballd` left running: it finds the runner in its front-dir, checks it
with the signed handshake and routes to it again, with no restart
(D-037).

**alarm** — a signal's reading outside one of its **bands**, held by the
rig as a condition on the signal: `band_warning` (outside `warning`) or
`band_alarm` (outside `alarm`), never both; or `band_unknown`, a banded
signal with no value because of a fault, per its `on_no_value`. An alarm is
not a fault: `/api/health` counts it in `alarms`, never in `ok`. A widget's
limits only colour what it draws.

**band** — a signal's `warning` or `alarm` range, `[lo, hi]`, set by the
driver or the rig file. The rig raises its **alarm** on the first reading
beyond it and clears it only after readings have been back inside for
`max(2·poll_s, 1 s)`.

**caveat** — what annotates a usable (`ok`) value and gates nothing:
`at_limit` (the sensor railed, or the rig clamped a demand, at that end),
`out_of_range` (outside the signal's `range`).

**cancelled** — how a program ends when a person ends it: the cancel
button, `POST /api/programs/cancel`, a new program started with `cancel`,
or cancelling what it waits on. Outputs are kept. Compare **interrupted**.

**cause** — why a **latch** holds: `stop` (the rig stop) or
`on_fault:<controller>` (a controller's `on_fault` action). Causes form a
set per subject; each is cleared only by its own **Reset**.

**command** — a non-value action on a device: a method marked `@command`,
run by `POST /api/devices/{name}/commands/{command}`. One that moves a
demand no argument is linked to declares it with `writes=`, and is refused,
like one with a `mode`, while a controller drives the device. A program's
steps are **steps**, not commands.

**condition** — something true *now* of a device, a signal, a controller
or the rig: offline, slow, a failing write, a held controller. Held in the
rig's condition store (`rig.conditions`), keyed by the object it is true of
and its `code`, while it lasts; its start and end are events (**edges**).

**edge** — the event that records a condition starting (`raised`) or ending
(`cleared`, with how long it held). A point event has none.

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

**delivery** — one call to `rig.on_samples`: the input bindings whose
source got a reading are told (their devices' `inputs_changed`, and marked
touched), then controllers tick, then one commit per device touched, then
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
offline, an activity timed out. A point in time with a `code`, a `severity`
(`debug`, `info`, `warning`, `error`), a scope, a subject and, for a
condition's start or end, an **edge**; streamed on `/ws/events` and written to the session when
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

**input binding** — an input once the rig has resolved it: an
`InputBinding`, following a source signal (or namespace) or holding a
number, with its value, quality, reason and age read through to the
source. A device's inputs are its bindings; a program step or a
controller may hold one too (`rig.follow`). `rig.consumers(signal)` lists
the bindings that follow a signal.

**inputs** — what a device follows, by the input's name: another device's
signal or a number, `inputs: {dry: hum_sensors.dry.humidity, wet: 88.5}` in
the rig file, an `Input` descriptor in the driver. No default: every
declared input is given one, or the file does not load. An address is
`pending` until its source's first reading. When the source lands the rig
tells the device and commits it, and it reads the value itself
(`self.<input>.value`) — there is no callback.

**interrupted** — how a program ends when the engine ends it (a software
stop, a shutdown, a latching fault: `fault:<controller>`), with the reason;
ending the program leaves outputs as they are and cancels the long device
command its step is running (a stop then writes its own values). A controller put in
manual by a device command is interrupted too -- only once the command has
succeeded, and the command's response names it. Compare **cancelled**.

**handover** — entering regulation, or changing a tuning: choosing what the
correction should be at the instant of the switch (a `Transfer`).

**keep** — the stop value that means "leave this output as it is",
energised if it was: `stop: {drive: keep}` in the rig file, and what a
stop does to an output with no `stop:` value and no driver **off**. Also
`on_shutdown: keep`: write nothing on the way out.

**key** — what a name is: lower-case letters, digits and `_`, starting with a
letter, at most 64 characters. `-` is read as `_` (`wet-pump` is `wet_pump`),
so two names that differ only by `-`/`_` are one name. A name is a key; its
display text is a **label**.

**latch** — what a software stop or an `on_fault` action leaves behind:
a **cause** and the subjects it holds (the rig, a device, a signal, a
controller), refusing writes and `regulate` until a person's **Reset**.
Kept in the store, so a restart keeps it and writes its stop again.

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

**rig edit** — a change to the rig made through the API, the UI or MCP: a
link or a device added or removed, a document added, a version restored.
Never applied in place (D-051): saved as a new rig version and, for a rig
from files, in the runner's own overlay `<first file>.d/added.<suffix>`;
then the rig is stopped and the runner restarts from that version, passive.

**measured** — a controller's measured signal: the published signal it
regulates (ISA's PV), `measured:` in the rig file. Also the faceplate row
and the wire field holding its last reading.

**off** — a demand's inactive level, declared by its driver
(`SignalSpec(off=...)`) only where it cannot be wrong: a PWM duty's 0 %.
What a stop writes when the rig file gives no `stop:` value. Written even
outside `limits`. Whether it de-energises the load depends on the wiring.

**on_fault** — what a controller does once its measured signal has been
faulty for its wait: `freeze` (the default: the law stays frozen, the
output where it was, and it resumes by itself), `manual` (to manual,
latched), `stop` (to manual, its output's **resolved stop** written, the
output latched), `stop_device` (the output's whole device stopped and
latched), or `{freeze_s, then}` (frozen that long, then one of the others).

**output** — a controller's output: the demand it writes (ISA's OP); the
controller is named by its address. Also the faceplate row and the wire
field holding the last value asked of it. Not a role: a signal a device
produces is a **readout**.

**permissive** — a condition on a demand (`permissive:` on the device):
a write is refused unless another signal's value is inside a band; it
fails closed, always permits the demand's **resolved stop** value, and
holds (not fails) a controller driving it (`not_permitted`).

**plant** — the thing being controlled, as a model: gain, time constant,
dead time (simulation only).

**planned stop** — the stop a rig edit runs before its restart: the same
writes and controllers to manual as a **software stop**, but nothing
latched, so the rig comes back passive.

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
holds, beside what was last written. Not a role; compare **readout**. A
demand's `readback` is `echo` (the value the rig committed, pushed back;
`stale(write_failed)` while its device's writes fail) or `sensed` (read
back from the hardware).

**reading** — one value on one signal at one instant; the value may be
absent (**no value**), with its **quality**.

**no value** — what a reading carries when it has no number: `null` on the
wire, `NoValue` in code, a NULL with a **flag** in the store. Never a
number standing in; a chart breaks there.

**quality** — what a signal's value is worth now: `ok`, `pending` (nothing
read yet), `not_applicable` (undefined now; shown "n/a"), `invalid` (read,
not a valid measurement) or `stale` (the last value is no longer trusted,
with a reason: `device_offline`, `device_hung`, `silent`, `last_read`,
`never_read`, `write_failed`). `pending` and `not_applicable` are benign;
`invalid` and `stale` are faults.

**liveness** — whether a published measurement is still arriving, judged
by the rig on its own clock: nothing for its **stale_after_s** (default
`max(3·poll_s, 5 s)` while polled) and the rig pushes `stale` on it
(`silent`, `last_read`, `never_read`).

**hung** — a condition on a device whose poll is stuck in a read past
`max(3·poll_s, 5 s)`; what its reads delivered is `stale(device_hung)`
until the read returns.

**fault time** — how long a controller's measured signal has been a fault
(`invalid`, `stale`) in one outage, accrued on the rig clock; a reading
with a value pauses it, 3 in a row end the outage. Reaching its wait
*releases* the outage, where `on_fault` will act.

**re-apply** — a controller following a moving setpoint writing its
feedforward plus the last correction between readings, every
`setpoint_period_s`; the law steps only on readings. Recorded as a tick
with `reapplied`.

**frozen** — a condition on a regulating controller whose measured signal
has no value: the law does not step, nothing is written, until 3 readings
in a row have one. Not a mode.

**flag** — a stored reading's code: 1-4 a no-value's quality (invalid,
not_applicable, stale, stale with the device offline), 16/17 the mark
`at_limit` low/high on a value.

**Reset** — a person's `POST /api/rig/reset {cause}`: lets one **latch**
go. Needs `operate`, and a person (never an agent or a service token).
Resumes nothing: controllers stay in manual, programs stay ended.

**resolved stop** — what a stop does to a device: its **stop command**
if the driver has one; otherwise, per writable demand, the rig file's
`stop:` value, else the driver's **off**, else **keep**.
`GET /api/rig/stop` lists it per output.

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
or `Role.SETTING` (re-set by a command; changes how the device behaves).
Sets the signal's default access. A number a device is built from is not a
signal, and an input is not a role: it is a binding to another device's
signal, or a number.

**sample** — every signal under one node at one instant, keyed by the
bound signal objects.

**session** — one recording: spans, samples, ticks, write states, events,
the config and tuning in force.

**setpoint** — the reference resolved at an instant, in the measured
signal's unit; the faceplate's middle row (ISA's SP).

**setting** — a signal re-set by a command while a device runs, shown but
not driven by a controller (`Role.SETTING`, `RP`); a `values` device's
entries are settings an operator writes (`RPW`).

**settle** — a program step: wait until the named controllers sit within a
band of their setpoints for `count` consecutive readings, or time out.
Registers as an **activity** named `settle:<controllers>`.

**signal metadata** — a signal's descriptive and limiting fields (label,
range, precision, warning, alarm, limits, poll_s, stale_after_s, max_rate,
tags, record), from the driver and then the rig file's `signals:` (`SignalMeta`,
`NamespaceMeta`); applied in place, without a rebuild.

**shape** — which door a front has, set by `auth:`: `local` (no sign-in,
loopback only), `password` (an admin password and named tokens) or
`proxy` (an identity proxy in front).

**signal** — one named value of one quantity on one device; has an address,
a role and an access set.

**software stop** — the **Software stop** button, `POST /api/rig/stop`,
`flyball stop`, MCP `stop_rig` or `SIGUSR1`, all one: the rig **latched**,
running long commands cancelled, the program interrupted, every controller
to manual, then each device's **resolved stop** applied. A control
function: it sets only the outputs whose level it knows
([What flyball does not do](0-overview/limits.md)).

**stop command** — a device command marked `@command(stops=True)`: the
device's stop, run by a software stop, a shutdown and `on_fault: stop`
instead of writing values (the humidity blender's `stop`, `mcp4725`'s
`power_down`). At most one per driver; never `long`.

**stopped** — the rig while the software stop's **latch** holds (the
condition `stopped`): flyball refuses its own automatic writes. It does
not mean the outputs are off.

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
a law, a feedforward or a setpoint generator entry is (`type: pi`,
`type: sim_plant`). A device names its implementation with `driver:`
instead; a program step has no discriminator (its key is the step).

**values device** — a `driver: values` device: numbers an operator
enters, each a setting (`rpw`) other devices' inputs may follow; published
from build, never stale, left alone by a stop, and its last write kept in
the store's `live_value` table and restored while the rig file's `initial`
is unchanged.

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
