# Writing programs

A program file is YAML (or JSON, or TOML): a name and a list of steps.

```yaml
name: bake
steps:
  - regulate: {loop: heaters.heater1, setpoint: 100}
  - ramp: {loop: heaters.heater1, to: 150, per_minute: 2}
  - wait: "Open the door and load the sample"
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
- ramp: {loop: heaters.heater1, to: 60, per_minute: 2}    # pace: {per_minute: 2}
- ramp: {loop: heaters.heater1, to: 60, minutes: 10}      # pace: {minutes: 10}
- hold: {minutes: 10}                                     # duration: {minutes: 10}
```

With two such fields the flat keys would be ambiguous, so the nested form is
required.

## Modifiers

An application may declare keys allowed beside any command — a completion
test, a duration, a flow — in its **dialect**. They are validated as part of
the step and handed to the programmer with the command:

```yaml
- regulate: {loop: heaters.heater1, setpoint: 100}
  note: "start of the soak"
```

Which modifiers exist is the application's business; the library defines
none.

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
wait or hold that times out, ends the program and is recorded as an event
(`/api/events`, `/ws/events`) with the program name and step index.

A step that raises -- a controller, device or signal not found, a conflict
with the rig's state, or any other exception from the step itself -- ends
the program in a distinct `failed` state rather than finishing quietly: an
ERROR `step_failed` event names the step, and a second ERROR `failed` event
(in place of the usual `finished`/`interrupted`) closes the run, both
carrying the exception's text. Steps after the one that raised do not run.
`GET /api/programs/running` keeps reporting `failed: true` and the error,
even after the run has ended, until the next `run`/`start` clears it; a run
started over HTTP still gets the failure back as the request's error detail,
same as it always has for a step that cannot even be applied.

## The commands every rig has

| tag | arguments | does |
| --- | --- | --- |
| `regulate` | `setpoint` (primary), `loop?`, `tuning?` | aim a controller at `setpoint` and let its law drive; returns at once |
| `ramp` | `to` (primary), `pace` as `per_minute: 5` or `minutes: 20` flat, `loop?`, `wait=True` | walk the setpoint to `to`; waits for arrival unless `wait: false` |
| `hold` | `duration` (primary, `minutes: 10` flat), `message?`, `timeout?` | keep everything as it is; controllers go on regulating |
| `arrive` | `loop?` (primary), `within=1.0`, `readings=3`, `timeout?`, `message?` | wait until the named controllers settle within `within` of their setpoints for `readings` consecutive readings |
| `manual` | `loop?` (primary) | stop a controller regulating; its target keeps its last demand |
| `set` | `device`, `values: {name: value}` | put `values` on `device`'s writable signals, as one demand |
| `command` | `device_command`, `device`, `args?` | call one of `device`'s own commands, exactly as `POST /api/devices/{name}/{tag}` would |
| `wait` | `message` (primary), `name?`, `timeout?` | pause until `POST /api/waits/{name}/fire`; a timeout ends the program |

`loop` is a controller's name -- the address of the signal it drives -- a
list of names, or absent for the rig's default. It stayed `loop` as a field
name through the device-model rewrite even though the concept is now called
a controller (`flyball.control.Controller`): a writable signal has at most
one controller, so naming it by the target's address is unambiguous. A ramp
over several controllers returns when the longest arrives, unless
`wait: false` starts every ramp and moves on at once -- `arrive` can wait for
them later. Durations and rates count in the rig's clock: on a simulation at
60× a ten-minute hold takes ten seconds, and on a stepped clock it takes no
time at all with every poll in between still happening.

`hold`'s `timeout` ends the program if `duration` itself never elapses (a
stalled clock, say), exactly like `wait`'s -- but as a plain number of
seconds, not a duration: `duration` is already the one field a program file
may write flat (`hold: {minutes: 10}`), and a second duration-typed field
would make that ambiguous.

`set` reaches a device directly rather than through a controller: it is
`rig.demand` in a step, and fails the same way a demand does -- 409 for a
signal a controller drives, a `together` group set in part, or a signal
that is not writable.

`command`'s `device` names any device on the rig, readable or writable,
since names are unique rig-wide. This is also how a program reaches a
simulated device's own commands (`fail`, `restore`, `disturb`,
`set_limits`) -- ordinary commands on the device, just as the Simulation tab
calls them by hand. `device_command`, not `command`: every step's wire form
reserves `command` for the step's own tag.

## A worked example

`examples/simulated/furnace.yaml`'s `programs/firing.yaml` -- a firing on
all three of the furnace's zones, quoted as the file actually is:

```yaml
name: firing
steps:
  - regulate: { loop: [heaters.heater1, heaters.heater2, heaters.heater3], setpoint: 20 }
  - ramp: { loop: [heaters.heater1, heaters.heater2, heaters.heater3], to: 600, per_minute: 10 }
  - hold: { minutes: 20, message: "soak at 600" }
  - ramp: { loop: heaters.heater2, to: 900, per_minute: 5 }       # the middle only: the neighbours fight it
  - hold: { minutes: 15, message: "soak at 900" }
  - ramp: { loop: [heaters.heater1, heaters.heater2, heaters.heater3], to: 100, per_minute: 20 }
  - manual: [heaters.heater1, heaters.heater2, heaters.heater3]
  - wait: { message: "unload the sample, then press go", timeout: { minutes: 10 } }
```

`heaters.heater1`/`heater2`/`heater3` are the controllers driving the
furnace's three zone heaters -- named by the writable signal each drives, not
by the thermocouple it reads (`furnace.zone1`, its `signal`). `flyball
program check programs/firing.yaml` validates it against a running rig;
`flyball-daemon furnace.yaml` and then `POST /api/programs/run` with the
file's body runs it, at the file's 60× clock a two-hour firing in two
minutes. The same directory's `step-test.yaml` and `load-sample.yaml` are
worked examples of `regulate` used as a step change (autotuning) and of
`wait` used for an operator prompt.
