# Internals

!!! abstract "Where you are: Internals"
    For the **contributor** changing flyball itself: how the core, the runtime, the controller and the store are built, and why.

    | if instead you want to… | go to |
    | --- | --- |
    | understand the words first | [Overview](../0-overview/index.md) |
    | operate a rig that is already set up | [Running a rig](../1-running/index.md) |
    | describe a rig: devices, links, controllers, how it is served | [Configuration](../2-config/index.md) |
    | drive hardware nothing here supports, or add a law | [Extending](../3-extending/index.md) |
    | talk to a rig from your own code or a model | [The server](../4-server/index.md) |
    | look a key or a route up | [Reference](../7-reference/index.md) |

| page | |
| --- | --- |
| [Architecture](architecture.md) | the layers, what may import what, the extension points |
| [Core](core.md) | time, units, signals, devices, errors, config |
| [Runtime](runtime.md) | the rig, polling, delivery, the router, recording |
| [The controller in detail](controller.md) | state, the tick, feedforward, the reference, handover, anti-windup |
| [Identification](identification.md) | fitting a plant from a controller's own samples; the tuning rules |
| [Storage](db.md) | the store's two faces, what a session holds, SQLite and migrations |
| [Decisions](decisions.md) | the numbered record of options weighed |

The server's own internals are in [How the server is built](../4-server/internals.md).
