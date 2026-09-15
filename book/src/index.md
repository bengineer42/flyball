# Flyball

Flyball controls *a quantity* with *an actuator*. It provides the control
loop, the control laws, a runtime with a timebase and telemetry, plant
identification, and a daemon with a CLI and UI on top. It knows nothing about
any particular sensor, actuator or board — those are written against the
protocols this book describes.

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

Every command in this book runs against the simulated plant
(`flyball.sim`), so no hardware is needed to follow it. For a rig with real
hardware, see [Worked examples](examples.md).
