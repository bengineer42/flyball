# Running a rig

!!! abstract "Where you are: Running a rig"
    For the **operator**: the rig is set up, and you drive it -- watch, control, record, run programs. Nothing here changes what the rig is.

    | if instead you want to… | go to |
    | --- | --- |
    | understand the words first | [Overview](../0-overview/index.md) |
    | describe a rig: devices, links, controllers, how it is served | [Configuration](../2-config/index.md) |
    | drive hardware nothing here supports, or add a law | [Extending](../3-extending/index.md) |
    | talk to a rig from your own code or a model | [The server](../4-server/index.md) |
    | change flyball itself | [Internals](../6-internals/index.md) |
    | look a key or a route up | [Reference](../7-reference/index.md) |

| section | |
| --- | --- |
| [Starting a rig](runner/index.md) | `flyball-runner rig.yaml`; what it serves; [access](runner/access.md) (the door, a sub-path, stopping); [building a rig while it runs](runner/building.md) |
| [The UI](ui/index.md) | the pages, the app bar, then a page each for [devices](ui/devices.md), [controllers](ui/controllers.md), [charts](ui/charts.md), [sessions](ui/sessions.md), [the Rig page](ui/rig.md), [dashboards](dashboards.md) |
| [Programs](programs/index.md) | what a program is; [writing and running one](programs/writing.md) |
| [The CLI](cli/index.md) | `flyball status`, `flyball devices`, `flyball invoke <device> <command>` |
| [Tuning and autotune](autotune.md) | a step test or a relay test, and the tuning it writes |

Everything on these pages is a client of [the server](../4-server/index.md);
what the rig *is* was decided in [Configuration](../2-config/index.md).
