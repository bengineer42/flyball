# Integrations

!!! abstract "Where you are: Integrations"
    What flyball plugs into today -- instruments, libraries, boards, data tools, models, your own code -- one page each: what it is, what comes through, how it is configured, where the code is. For anyone asking "does it talk to X?".

    | if instead you want to… | go to |
    | --- | --- |
    | understand the words first | [Overview](../0-overview/index.md) |
    | operate a rig that is already set up | [Running a rig](../1-running/index.md) |
    | describe a rig: devices, links, controllers, how it is served | [Configuration](../2-config/index.md) |
    | drive hardware nothing here supports, or add a law | [Extending](../3-extending/index.md) |
    | talk to a rig from your own code or a model | [The server](../4-server/index.md) |
    | change flyball itself | [Internals](../6-internals/index.md) |
    | look a key or a route up | [Reference](../7-reference/index.md) |

| | status | page |
| --- | --- | --- |
| SCPI over VISA / serial; Modbus TCP / RTU | `flyball-visa`, `flyball-modbus` | [Instrument protocols](protocols.md) |
| QCoDeS (~200 drivers), PyMeasure (~150) | `flyball-qcodes`, `flyball-pymeasure` | [Instrument libraries](libraries.md) |
| Raspberry Pi: I²C, SPI, GPIO, PWM, 1-Wire and the chips on them | `flyball-linux`, `flyball-chips` | [Raspberry Pi and Linux buses](linux.md) |
| Bluesky: readables and movables over any signal; event-model export in `flyball.record.documents` (shipped, not gated on the extra) | `flyball-bluesky` | [Bluesky](bluesky.md) |
| A model over MCP (Claude Desktop, Claude Code, any MCP client) | shipped, on the runner's port | [Models over MCP](models.md) |
| Your own code: Python client, TypeScript client, plain HTTP | shipped | [Your own code](code.md) |
| Applications built on flyball | example: [the humidity rig](https://bengineer42.github.io/humctrl/) | [Applications](applications.md) |
| EPICS, OPC UA, NI-DAQmx, LabJack, vendor packages | not yet | [Not yet](not-yet.md) |

Everything here is a `Device` like any other once attached: routes, a schema,
telemetry, CLI subcommands and a client method per command with no further
code. Where a new one goes: a driver is [Extending](../3-extending/index.md);
a link tag, [Config and build](../3-extending/device/config.md); a data tool,
[The server](../4-server/index.md).
