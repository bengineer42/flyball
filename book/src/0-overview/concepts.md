# What you will see

Six words cover everything on the screen. Each is a thing you can point at
in the UI; the deeper vocabulary behind them lives with the people who need
it -- [The device model](../3-extending/model.md) for the developer,
[The controller in detail](../6-internals/controller.md) for the contributor.

**The rig** is everything one runner serves: the page you open. It has a
name (`furnace`, `humidity`), a clock (real time, or faster on a
simulation), and at any moment it is either recording or not.

**A device** is one thing on the rig -- a sensor, a heater, a bench
instrument, a pump blender -- and one card on the Devices page. A device
shows its **signals**, its **commands** (buttons with a form: `set_limit`,
`off`, `fail`) and its **conditions** (what it says is wrong with itself
now: offline, railed).

**A signal** is one value on one device, with a unit: `furnace.zone1`
reads 412.0 °C. Its **address** -- device, then dots, then name -- is
written on every readout and is the same everywhere: the chart legend, the
export's column, the program step, the CLI. Some signals are only read;
some can be **set** (a target box on the card); a signal that can be set is
what a controller drives.

**A controller** holds one signal at a **setpoint** by driving another: the
faceplate on the Controllers page, with a *reading* bar, a *target* box and
an *output* bar. It is in **manual** (holding whatever was last set) or
**regulating** (working towards the setpoint, which may be a value or a
ramp). The name of a controller is the address of the signal it drives.

**A program** is a list of steps run against the rig -- *regulate this to
400*, *ramp to 800 at 2 °C/min*, *wait 30 min*, *prompt for the door* --
written as a file, shown as steps on the Programs page, run and stopped
from there, the CLI or the API. A single command from the CLI is a program
of one step.

**A session** is a recording: everything the rig read, was told and did
between a start and an end, on the Sessions page, exportable as CSV, JSON
or a zip. While nobody is recording the runner still keeps the last hour --
the *scratch record* -- so a chart is never empty and a moment worth keeping
can be kept after the event.

## How it fits together

```
 config file ──▶ flyball-runner ──▶ the rig ──▶ /api, /ws, /mcp ──▶ the UI
 (links, devices,   builds and       reads, drives,                  the CLI
  controllers,      serves it        regulates, records               a script
  runner: how)                                                        a model
```

The config file says what is on the rig and how it is served
([Configuration](../2-config/index.md)); the runner builds the rig from it
and serves it ([Starting a rig](../1-running/runner/index.md)); everything
that faces a person -- the UI, the CLI, a script, a model -- is a client of
the same API ([The server](../4-server/index.md)).

**Real and simulated are the same rig.** A simulation overlay swaps the
links and drivers under the same device and signal names, so every address,
controller, program, dashboard and session is identical with and without
hardware: build and test with nothing plugged in, then run on the bench
([Simulation](../2-config/simulation.md)).
