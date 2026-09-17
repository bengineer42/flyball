# Applications built on flyball

An application is a package that depends on `flyball`: its own drivers
(registered through the `flyball.configs` entry point), rig files for the
hardware and for a simulation of it, programs, dashboards, tunings, and its
own book. Nothing in flyball knows it exists; everything in flyball works on
it.

| application | is | book |
| --- | --- | --- |
| **Humidity** (`examples/humidity`) | a chamber held at a target relative humidity by blending two air lines: Raspberry Pi, a TB6612 motor driver, SHT4x sensors; the `dual_pump_blender` driver and the `sim_humidity_chamber` plant | `examples/humidity/book/` |

The example rigs with nothing plugged in (`examples/simulated`: an oven, a
tank, a bench, a furnace) are not applications -- they are rig files only --
and are the place to start: [Simulation](../2-config/simulation.md).
Writing an application: [Extending](../3-extending/index.md), and
[Packaging](../3-extending/packaging.md) for how it ships.
