# Writing programs

A program file is YAML (or JSON, or TOML): a name and a list of steps.

```yaml
name: bake
steps:
  - regulate: {loop: heater, at: 100}
  - linear_ramp: {loop: heater, end: 150, per_minute: 2}
  - wait: "Open the door and load the sample"
  - wait: {message: "Close the door", timeout: {minutes: 5}}
```

## A step

Each step is a mapping with **one command key** — the command's tag — and
optionally modifier keys beside it. Any other key is an error: unknown keys
are how typos hide.

The value under the command key is either:

- **a mapping**: the command's arguments, verbatim;
- **a scalar or list**: shorthand for the command's *primary* field. `wait`'s
  primary is `message`, so `- wait: "Load the sample"` is
  `- wait: {message: "Load the sample"}`. A command with no primary takes no
  shorthand.

## Time

A duration is written as a mapping of unit keys, which add:
`{seconds: 90}`, `{minutes: 1, seconds: 30}`, `{hours: 2}`. A plain number is
seconds. A rate is one key naming the unit: `{per_minute: 2}`,
`{per_second: 0.01}`.

When a command has **exactly one** duration-or-rate field, its keys may be
written flat, beside the other arguments:

```yaml
- linear_ramp: {loop: heater, end: 60, per_minute: 2}     # pace: {per_minute: 2}
- linear_ramp: {loop: heater, end: 60, minutes: 10}       # pace: {minutes: 10}
```

With two such fields the flat keys would be ambiguous, so the nested form is
required.

## Modifiers

An application may declare keys allowed beside any command — a completion
test, a duration, a flow — in its **dialect**. They are validated as part of
the step and handed to the programmer with the command:

```yaml
- regulate: {loop: heater, at: 100}
  settle: {within: 0.5, readings: 5}
```

Which modifiers exist is the application's business; the library defines
none.

## Loop commands

| tag | arguments | does |
| --- | --- | --- |
| `regulate` | `loop`, `at`, `generator?`, `tuning?`, `transfer?` | aim the loop at `at` and hand control to the law; `at` is a number or `process`, `setpoint`, `demand` |
| `linear_ramp` | `loop`, `end`, `pace`, `start?` | walk the setpoint from `start` (default: the reading) to `end` at `pace`, a duration or a rate |
| `update_setpoint` | `loop`, `value` | move the setpoint without touching the law |
| `settle_above` | `channel`, `above`, `margin?`, `duration`, `readings?`, `timeout` | wait until readings stay above the value |
| `settle_below` | `channel`, `below`, … | the same, below |
| `settle_at` | `channel`, `at`, `tolerance`, … | the same, within a tolerance |
| `wait` | `message`, `timeout?` | pause until someone fires the signal named `wait` |

`tuning` is a name registered on the rig or an inline law config
(`{tag: PI, kp: 1, ki: 0.1}`). `transfer` is `track` (default: bumpless),
`carry`, `reset` or `none`; see [Handover](../4-control/loop.md#handover).

Device commands are steps too, under the actuator's command tag with the
same arguments the HTTP route takes.

## Validation in an editor

The file's JSON schema is generated from the command registry, so an editor
validates exactly what the rig accepts. Emit it and point the YAML language
server at it:

```yaml
# yaml-language-server: $schema=./program.schema.json
```

## Running one

```python
from flyball.programmer import Programmer
from flyball.server.dialect import Dialect, program_from_file

program = program_from_file("bake.yaml", Dialect(commands=Commands))
Programmer(rig).run(program)          # blocks until done or interrupted
```

`Programmer.start(program)` returns at once and runs on a worker thread;
`interrupt()` stops it. The first step is applied on the calling thread, so
an unapplicable command raises there rather than disappearing into a log.

Over HTTP the same document goes to `POST /api/programs/run`;
`POST /api/programs/check` normalises and validates it without running, and
`flyball program check FILE` is that from the shell. A step that fails, or a
wait that times out, ends the program and is recorded as an event
(`/api/events`, `/ws/events`) with the program name and step index.

## The commands every rig has

| step | arguments | does |
| --- | --- | --- |
| `regulate` | `setpoint` (primary), `loop?`, `tuning?` | aim a loop and let its law drive; returns at once |
| `ramp` | `to` (primary), `pace` as `per_minute: 5` or `minutes: 20` flat, `loop?` | walk the setpoint there and wait until it arrives |
| `hold` | `duration` (primary, `minutes: 10` flat), `message?` | keep everything as it is; the loops go on regulating |
| `manual` | `loop` (primary) | stop a loop; its actuator keeps its demand |
| `wait` | `message` (primary), `name?`, `timeout?` | pause until `POST /api/signals/{name}/fire`; a timeout ends the program |

`loop` is a name, a list of names, or absent for the rig's default. A ramp
over several loops returns when the longest arrives. Durations and rates
count in the rig's clock: on a simulation at 60× a ten-minute hold takes ten
seconds, and on a stepped clock it takes no time at all with every poll in
between still happening.

```yaml
name: firing
steps:
  - regulate: { loop: [heater1, heater2, heater3], setpoint: 20 }
  - ramp: { loop: [heater1, heater2, heater3], to: 600, per_minute: 10 }
  - hold: { minutes: 20, message: "soak at 600" }
  - ramp: { loop: heater2, to: 900, per_minute: 5 }
  - manual: [heater1, heater2, heater3]
  - wait: { message: "unload the sample, then press go", timeout: { minutes: 10 } }
```
