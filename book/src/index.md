# Flyball

Flyball controls *a quantity* with *an actuator*. It provides the control
loop, the control laws, a runtime with a timebase and telemetry, plant
identification and tuning, recording, and a daemon with a CLI on top. It knows
nothing about any particular sensor, actuator or board: those are written
against the protocols this book describes, and everything above them — the
loop, the HTTP API, the command line, the program dialect — comes for free.

## Which part to read

| You want to… | Read |
| --- | --- |
| understand the vocabulary before anything else | **Concepts** |
| put flyball on your own hardware | **Building an application** |
| run a rig someone else has built | **Running** |
| tune a loop, add a control law, or fit a plant | **Control** |
| change flyball itself | **Internals** |
| look something up | **Reference** |

Parts are independent. Nothing in **Running** requires **Internals**;
nothing in **Building** requires **Control**.

Most bench equipment needs no code at all: a SCPI or Modbus instrument is
a few lines in a rig file, and QCoDeS and PyMeasure drivers wrap directly.
See [Supported equipment](3-running/equipment.md).

Every example in this book runs against a simulated plant: a first-order lag
standing in for an oven, read by a fake probe, driven by a fake heater. No
hardware is needed to follow it. The whole example is one file,
[`oven.py`](snippets/oven.py), and it is what the CLI and API examples talk to.
For a rig with real hardware, see [Worked examples](examples.md).

## Status

Flyball is pre-1.0 and used on one rig. The library, loop, laws, runtime,
recording, HTTP API, CLI, rig files and the generic SCPI/Modbus devices are
in place and tested. Programs — sequences of
commands run against a rig — exist as a vocabulary and a file dialect, but
the route that runs them is not mounted yet. Where a chapter describes
something designed but not built, it says so.
