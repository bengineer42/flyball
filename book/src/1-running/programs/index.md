# Programs

A **program** is an ordered list of commands run against a rig. A single
command is a program of one step, so there is one execution path and one
answer to "what is running".

## Steps

A step is a `Step`: a frozen dataclass with a `run(rig)` method, `tag`-named on the
class and registered explicitly with `catalog.register_step` (see
[Packaging](../../3-extending/packaging.md)) so a program file can use it; its
wire model — the request a client sends, the step a program file holds — is
derived from its constructor. The vocabulary shipped with the library:

| tag | does |
| --- | --- |
| `regulate` | aim a controller at a setpoint and hand control to the law |
| `ramp` | walk a controller's setpoint to a target at a pace, and wait until it arrives |
| `wait` | keep everything as it is for a duration; controllers go on regulating |
| `settle` | wait until named controllers have settled within a band of their setpoints |
| `manual` | stop a controller regulating; its target keeps its last demand |
| `set` | put values on one device's writable signals, as one demand |
| `command` | call one of a device's own commands |
| `prompt` | pause until someone fires a named signal |

`regulate`/`ramp`/`wait`/`settle`/`manual` name a **controller** by the
address of the signal it drives (or a list, or none for the rig's default) --
not the device itself, since a writable signal has at most one controller.
Device commands (`@command` methods on a device) are reachable as `command`
steps; a humidity rig's `set_blend` is one. Its `blend` is a `Setting`
signal — shown on the wire, `RP` — but a setting is re-set by a command,
not a demand, so it stays a `command` step; only a `Demand` signal (`RPW`)
can be reached as a `set` step. As over HTTP, a `command` step that succeeds
on an offline device (`restore`, a reset) restarts its polling; one still
broken goes offline again with a fresh event.

## Activities and signals

A command that finishes at once returns nothing. One that takes time — a
ramp, a settle test, a prompt to the operator — returns an **activity**: a
signal the programmer waits on before the next step. While the wait lasts the
signal is registered by name, so a client can list what the rig is waiting on,
**fire** it (the operator pressed the button, or wants the wait skipped) or
**cancel** it (the program ends at this step, `cancelled`).

A signal settles exactly once, and says how: fired, timed out, or
interrupted.

A program ends `succeeded` (every step ran), `failed` (a step raised, or a
wait timed out), `cancelled` (a person cancelled it, or what it waited on)
or `interrupted` (the engine ended it: a software stop, a shutdown), with
the reason. Either way the outputs are kept where they are.

A `command` step on a long device command (a `dispense`, a `move`) runs
until the command returns; cancelling the program does not stop the
device. A cancel or a stop waits at most 5 s for the step to return and
then goes on, with a `step_still_running` event naming the step: it may
still act, so stop the device itself (its `stop` command) to end it.

## Running one step on its own

To do one thing now — put a loop in manual, ramp a setpoint, send a device a
command — without writing a program, use **Run a step** on the Programs page.
It opens the program editor's own palette and step forms in a dialog; pick a
step, fill it in (the rig checks it as you go, as it does a program), and press
**Run**. Nothing is saved to the library: it runs as an unstored program named
"one-off step", so it shows on the program chip and in Events like any other
run. Add more steps if you want a short sequence. If a program is already
running, the dialog says so and the button becomes **Cancel it and run**. The
button is off for a browser that may only read. Behind it is
`POST /api/programs/run` with the document as the body.

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
  - regulate: {controllers: heaters.heater1, setpoint: 100}
  - ramp: {controllers: heaters.heater1, to: 150, per_minute: 2}
  - prompt: "Open the door and load the sample"
```

The file form is translated into the request form before it is validated, and
the file's JSON schema is generated from the same command registry, so an
editor validates exactly what the rig accepts. The UI's editor is built on the
same schema: a **Steps** tab with a palette of step kinds and a form per step,
drag-and-drop to add and reorder, and a **Text** tab with the file as written,
each rewriting the other, with the rig's check shown per step as you edit
([The UI](../ui/index.md#pages)). [Writing programs](writing.md)
has the rules.
