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
`off`, `fail`) and its **conditions** (what is wrong with it now: offline,
railed, a sensor failed; each one's start and end is in the event log).

**A signal** is one value on one device, with a unit: `furnace.zone1`
reads 412.0 °C. Its **address** -- device, then dots, then name -- is
written on every readout and is the same everywhere: the chart legend, the
export's column, the program step, the CLI. Some signals are only read;
some can be **written** (a box on the card); a **demand** -- a signal whose
writing takes control of the process -- is what a controller drives.

**A controller** -- a control loop, not a device -- holds one **measured**
signal at a **setpoint** by writing an **output**, a demand on a device: the
faceplate on the Controllers page reads *Measured*, *Setpoint* and *Output*.
It is in **manual** (the output keeps whatever was last written) or
**regulating** (working towards the setpoint, which may be a value or a
ramp). The name of a controller is the address of its output.

**A program** is a list of steps run against the rig -- *regulate this to
400*, *ramp to 800 at 2 °C/min*, *wait 30 min*, *prompt for the door* --
written as a file, shown as steps on the Programs page, run and cancelled
from there, the CLI or the API. A single step from the CLI is a program
of one step.

**A session** is a recording: everything the rig read, was told and did
between a start and an end, on the Sessions page, exportable as CSV, JSON
or a zip. While nobody is recording the runner still keeps the last hour --
the *scratch record* -- so a chart is never empty and a moment worth keeping
can be kept after the event.

## How it fits together

```
 rig file ─────▶ flyball-runner ──▶ the rig ──▶ /api, /ws, /mcp ──▶ the UI
 (links, devices,   builds and       reads, drives,                  the CLI
  controllers,      serves it        regulates, records               a script
  runner: how)                                                        a model
```

The rig file says what is on the rig and how it is served
([Configuration](../2-config/index.md)); the runner builds the rig from it
and serves it ([Starting a rig](../1-running/runner/index.md)); everything
that faces a person -- the UI, the CLI, a script, a model -- is a client of
the same API ([The server](../4-server/index.md)).

**Real and simulated are the same rig.** A simulation overlay swaps the
links and drivers under the same device and signal names, so every address,
controller, program, dashboard and session is identical with and without
hardware: build and test with nothing plugged in, then run on the bench
([Simulation](../2-config/simulation.md)).
