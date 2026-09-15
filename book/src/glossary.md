# Glossary

**activity** — the ongoing part of a command: a signal a program step waits
on, that knows how to attach itself to the rig.

**actuator** — a device a loop drives. Has `set_demand`.

**bump** — the step a handover put through the actuator; zero when the seed
held the output.

**channel** — one measurand from one source. What a loop regulates.

**command** — something a program can run: a frozen dataclass with `run`,
registered by tag. Also, on a device, a method marked `@command`.

**condition** — something true of a device *now*: offline, railed,
overdriven. In its state while it holds.

**config** — what a device was built from; a pydantic model with `build()`.
Changed only by rebuilding.

**correction** — what the law produces: the offset added to the setpoint to
give the demand.

**delivery** — one batch of samples from a reader reaching the rig.

**demand** — `setpoint + correction`; what the actuator is told, in the
channel's unit.

**expected** — what the actuator says it will deliver; `None` if it cannot
say.

**handover** — entering regulation, or changing a tuning: choosing what the
correction should be at the instant of the switch.

**law** — a `ControlLaw`: `(elapsed, reading, setpoint) → correction`.

**link** — what a generic device talks over: a `TextLink` (SCPI, serial)
or a `RegisterLink` (Modbus), real or fake.

**loop** — one channel, one law, one actuator, one reference. Named after
its actuator.

**measurand** — what is measured: a name and a unit. Interned on the name.

**mode** — what a loop is doing: `manual`, `open`, `regulating`.

**observer** — hears samples or readings from named sources and channels.

**plant** — the thing being controlled, as a model: gain, time constant,
dead time.

**program** — an ordered list of commands. Data; no cursor.

**programmer** — runs a program against a rig, waiting where a step waits.

**reader** — a device that delivers samples for one or more sources.

**reading** — one value on one channel at one instant.

**reference** — where a loop is aiming: a value, or a generator.

**rig file** — a `.toml`/`.yaml`/`.json` file of links, readers, actuators
and loops that `load_rig` builds.

**rig** — the clock, the readers, the actuators, the loops, the cells, the
signals, the recorder. What the equipment *is*.

**sample** — every measurand of one source at one instant.

**session** — one recording: spans, samples, ticks, events, the config and
tuning in force.

**setpoint** — the reference resolved at an instant.

**settings** — what a command can change while a device runs.

**signal** — fires once, and says how it ended: fired, timed out,
interrupted.

**sink** — something that commits on `apply`: an actuator, or a buffering
observer.

**source** — one thing that emits readings. Declares its channels once.

**state** — what a device reports now; changes every tick or read.

**tick** — one loop step on one reading.

**transfer** — how a handover seeds the correction: `none`, `reset`, `carry`,
`track`.

**tuning** — a named law config.
