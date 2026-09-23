# Flyball

Flyball runs a lab rig: it reads the sensors, drives the actuators, holds a
quantity at a setpoint, walks it through a programme, records everything,
and shows the whole rig in a browser -- from one file that says what the
rig is made of. It knows nothing about any particular sensor, actuator or
board; those are written against a small device model, and everything above
them comes for free.

## Getting started

**I'm operating a rig someone has already set up.** Open its address in a
browser -- the UI is the whole rig: readouts, charts, controllers,
programs, sessions.

1. [The UI](1-running/ui/index.md) -- what each page is for, the app bar, the
   controller faceplate; [signing in](1-running/ui/index.md#signing-in) if
   the rig asks for one.
2. [Running a program](1-running/programs/writing.md#running-one) and
   [recording a session](1-running/ui/sessions.md#sessions), then
   [getting the data out](1-running/ui/charts.md#downloads).
3. [The CLI](1-running/cli/index.md) when a terminal is closer than a browser:
   `flyball status`, `flyball devices`, `flyball invoke <device> <command>`.

**I'm setting up a rig.** A rig is one file: what is on it and how it is
served. Start with a simulated one, then swap the links for real ones.

1. Install: `cd engine && uv sync --all-extras`; the UI is
   `cd ui && npm install && npm run build` (or `npm run dev` while working
   on a rig -- see [The UI](1-running/ui/index.md)); the `flyball` CLI is
   `cd daemon && ./build-with-ui.sh` (see
   [Installing](1-running/runner/index.md#installing)).
2. [Starting a rig](1-running/runner/index.md): `flyball run rig.yaml`, the
   dashboard at `http://127.0.0.1:8000/` with no sign-in; what it serves;
   [who may reach it](1-running/runner/access.md); `--record`.
3. [The config file](2-config/index.md): the annotated example, then
   [links](2-config/links.md), [devices](2-config/devices/index.md) and
   [controllers](2-config/controllers.md), each key on its own page.
4. [Supported drivers](2-config/devices/drivers.md): whether your instrument is a
   few lines of config (SCPI, Modbus, QCoDeS, PyMeasure, the Pi chips) --
   most are. [Simulation](2-config/simulation.md) to build it with nothing
   plugged in; [Boards](2-config/boards.md) for a Raspberry Pi.
5. [Tuning](1-running/autotune.md) once it runs; [dashboards](1-running/dashboards.md)
   and [programs](1-running/programs/writing.md) for the people who will operate it.

**I have hardware nothing here drives.** [Extending](3-extending/index.md)
-- one short class, and it gets everything above for free.

## What it does

| | |
| --- | --- |
| **Devices** | a sensor, an actuator, a bench instrument, a composite: each a tree of **signals** with an address (`furnace.zone1`), a unit, a role and what may be done with it (read, watch, write). SCPI, Modbus, QCoDeS and PyMeasure instruments need no code: a few lines in the config file |
| **Controllers** | one publishing signal regulated through one writable signal by a law (P, PI, PID, or your own) with feedforward, limits, bumpless handover between manual and automatic, autotune from a step or a relay test |
| **Programs** | a sequence of commands -- regulate, ramp, hold, arrive, set, wait -- written as a file, validated in an editor, run and interrupted from the API |
| **Recording** | every reading, demand and controller tick into SQLite as sessions; export as CSV, JSON or a zip; rig versions beside the data so a session always has its rig |
| **Simulation** | plants (lags, furnaces, tanks) and a clock that runs at 60× or in steps, so a rig, a program and a dashboard are built and tested with nothing plugged in, then run unchanged on hardware |
| **The server** | one runner per rig: an HTTP and websocket API, a browser UI rendered from the rig's own schema, a command line, a Python client, and an MCP server so a model can read or drive the rig |
| **Boards** | Raspberry Pi I²C, SPI, GPIO, PWM and 1-Wire links (`flyball-linux`) with chip drivers on them (`flyball-chips`), board profiles by name |

## The ideas, in one paragraph

A **rig** is a set of **devices** on **links** (a bus, an instrument
connection, a simulated plant). Every device has a tree of **signals**;
each signal has an **address**, a **quantity** (name and unit) and an
**access** -- readable, publishing, writable. A **controller** binds one
publishing signal to one writable signal through a **law**. A **program**
is a list of **commands** run against the rig. A **session** is everything
recorded between a start and an end. The **config file** declares the
links, the devices and the controllers; the **runner** builds the rig from
it and serves it. [What you will see](0-overview/concepts.md) puts the six
words an operator meets on the screen; the parts below go as deep as you need.

## Who reads what

| you | part |
| --- | --- |
| run a rig someone else has set up: watch it, drive it, record, run programs | [Running a rig](1-running/runner/index.md) |
| describe a rig -- which devices on which links, what regulates what, how the runner serves it | [Configuration](2-config/index.md) |
| put flyball on hardware it has no driver for, or add a control law | [Extending](3-extending/index.md) |
| talk to a rig from your own code, a script, or a model | [The server](4-server/index.md) |
| change flyball itself | [Internals](6-internals/architecture.md) |
| look something up | [Reference](7-reference/rig-file.md), [Glossary](glossary.md) |

The parts are independent. Nothing in *Running* needs *Internals*; nothing
in *Extending* needs *The server*.

## Trying it

Every example in this book runs against a simulated plant; no hardware is
needed. The quickest whole rig is a file:

```
cd examples/furnace
uv run flyball-runner rig.yaml      # a three-zone furnace at 60×
```

then open the UI (see [The UI](1-running/ui/index.md)) or, with the CLI
built ([Installing](1-running/runner/index.md#installing)), `flyball status`.
The oven the *Extending* chapters build in Python is
[`oven.py`](snippets/oven.py):

```
$ python oven.py
after 2 min: 100.9 °C, demand 95.4
```

A complete application on real hardware, with its own book, is
[the humidity rig](https://bengineer42.github.io/humctrl/) -- see [Worked examples](0-overview/examples.md).

## Status

Flyball is pre-1.0. The device model, the runtime, the control laws, the
HTTP API, the CLI, the UI, config files, recording and the generic
SCPI / Modbus / simulation drivers are in place and tested. Where a chapter
describes something designed but not built, it says so.
