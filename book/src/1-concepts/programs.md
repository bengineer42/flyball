# Programs

A **program** is an ordered list of commands run against a rig. A single
command is a program of one step, so there is one execution path and one
answer to "what is running".

## Commands

A command is a frozen dataclass with a `run(rig)` method. It self-registers
under a tag, and its wire model — the request a client sends, the step a
program file holds — is derived from its constructor. The vocabulary shipped
with the library:

| tag | does |
| --- | --- |
| `regulate` | aim a controller at a setpoint and hand control to the law |
| `ramp` | walk a controller's setpoint to a target at a pace, and wait until it arrives |
| `hold` | keep everything as it is for a duration; controllers go on regulating |
| `arrive` | wait until named controllers have settled within a band of their setpoints |
| `manual` | stop a controller regulating; its target keeps its last demand |
| `set` | put values on one device's writable signals, as one demand |
| `command` | call one of a device's own commands |
| `wait` | pause until someone fires a named signal |

`regulate`/`ramp`/`hold`/`arrive`/`manual` name a **controller** by the
address of the signal it drives (or a list, or none for the rig's default) --
not the device itself, since a writable signal has at most one controller.
Device commands (`@command` methods on a device) are reachable as `command`
steps; a humidity rig's `set_blend` or `set_fraction` becomes a `set` step on
the blender's own writable signals instead, once those settings are signals
rather than commands.

## Activities and signals

A command that finishes at once returns nothing. One that takes time — a
ramp, a settle test, a prompt to the operator — returns an **activity**: a
signal the programmer waits on before the next step. While the wait lasts the
signal is registered by name, so a client can list what the rig is waiting on,
**fire** it (the operator pressed the button, or wants the wait skipped) or
**interrupt** it (stop the program at this step).

A signal settles exactly once, and says how: fired, timed out, or
interrupted.

## Who owns what

The **rig** is what the equipment *is*: devices, controllers, the clock.
The **programmer** is what it is *doing*: the current program and the step it
is on. Keeping them apart means "abort the program" never tangles with "stop
the pumps", and each has its own lock.

A program is data. It holds no cursor and no running flag, so the same program
can be run twice, or twice at once on two rigs.

## Program files

A person writes a program as YAML: a name and a list of steps, each step the
command's tag as the key and its arguments as the value:

```yaml
name: bake
steps:
  - regulate: {loop: heaters.heater1, setpoint: 100}
  - ramp: {loop: heaters.heater1, to: 150, per_minute: 2}
  - wait: "Open the door and load the sample"
```

The file form is translated into the request form before it is validated, and
the file's JSON schema is generated from the same command registry, so an
editor validates exactly what the rig accepts. [Writing programs](../3-running/programs.md)
has the rules.
