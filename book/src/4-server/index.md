# The server

!!! abstract "Where you are: The server"
    For the **integrator** talking to a rig from a script, a program or a model: the HTTP and websocket API, the wire format, MCP.

    | if instead you want to… | go to |
    | --- | --- |
    | understand the words first | [Overview](../0-overview/index.md) |
    | operate a rig that is already set up | [Running a rig](../1-running/index.md) |
    | describe a rig: devices, links, controllers, how it is served | [Configuration](../2-config/index.md) |
    | drive hardware nothing here supports, or add a law | [Extending](../3-extending/index.md) |
    | change flyball itself | [Internals](../6-internals/index.md) |
    | look a key or a route up | [Reference](../7-reference/index.md) |
Every rig is served by one runner: a FastAPI app with an HTTP API, a set of
websockets, and -- on the same port -- the MCP server. Everything that
faces a person is a client of it: the browser UI, the `flyball` command
line, the Python client, a model over MCP, your own script. None of them
knows anything the API does not publish.

| page | |
| --- | --- |
| [HTTP and websocket API](api.md) | every route, by area: the runner, the rig and its composition, devices, reading, controllers, activities, history and export, programs, dashboards, simulation, events; the websockets |
| [Wire format](wire.md) | how a time, a signal, a unit, a device, a controller and an error are spelled in JSON |
| [The MCP server](mcp.md) | the three tiers a model may be given, and what each sees |
| [How the server is built](internals.md) | assembly, resolution at request time, the wire models, telemetry, the program dialect |

## Finding a rig

`http://127.0.0.1:8000` by default: the front `flyball run` starts, or a
bare `flyball-runner`. From another machine it needs a door with a sign-in
-- a front's `password` or `proxy` shape, or a bare runner's token
([Access](../1-running/runner/access.md)). Under `flyballd`
(`http://127.0.0.1:9000`) each rig is under its root path, `/furnace/api/…`,
as is a bare runner started with `--root-path /furnace`
([a sub-path](../1-running/runner/access.md#a-sub-path)). `GET /api/health` is the one-look status;
`GET /api/schema` describes every device; `GET /api/runner` says how the
process was started and what it allows.

## Clients

| client | is | uses |
| --- | --- | --- |
| the UI ([Running a rig](../1-running/ui/index.md)) | a React app rendered from `/api/schema`, live on the websockets | everything |
| `flyball` ([The CLI](../1-running/cli/index.md)) | a standalone Go binary (`daemon/cmd/flyball`), fixed subcommands rather than one per device | `/api`, `/ws` |
| `flyball.interfaces.client.Rig` | a pure HTTP client that synthesises a method per device command from the schema | `/api`, `/ws` |
| `@flyball/client` (`ui/packages/client`) | the same in TypeScript, typed from the wire format | `/api`, `/ws` |
| a model ([The MCP server](mcp.md)) | tools generated from the same routes | `/mcp/<tier>` |

The Python client is three lines:

```python
from flyball.interfaces.client import Rig

rig = Rig("http://127.0.0.1:8000")          # or "http://host/furnace" behind a prefix
print(rig.read("furnace.zone1"))             # a signal by address
rig.write("heaters.heater1", 0.4)           # a writable signal, directly
rig.post("/api/controllers/heaters.heater1/regulate", {"at": 400})   # any route
rig.devices["furnace"].fail(signal="zone1")  # a device command, checked against its schema
```

## Authentication and what is allowed

Open by default, on this machine only (the `local` shape). Behind a front
with a sign-in, a person's browser carries the session cookie a sign-in set,
and code sends a named token as `Authorization: Bearer`; `anonymous: read`
lets reads through regardless. Each route needs a verb, `read` or
`operate` (pending D-034), and a stop needs `operate`
([authentication](api.md#authentication)). A token never goes in a URL.
The Python client takes it as `Rig(url, token=…)` or `FLYBALL_TOKEN`.
Independently, some things are
off unless the runner was started allowing them: building up a hardware
rig (`--compose`), writing rig files (`--allow-save`), stopping or
restarting (`--allow-shutdown`), the MCP mount (`--no-mcp` turns it off).
All of it is in the [runner section](../2-config/runner.md) of the config
file, and [Starting a rig](../1-running/runner/index.md) says what each means in
practice, including how to put a rig on the public internet read-only.

## Errors

One shape for every failure: a status and `{"detail": "..."}`. 404 nothing
by that name, 409 wrong state (stop or start something and retry), 422 well
formed but the numbers are unachievable, 503 the rig is not ready or a
device failed. [Errors](api.md#errors) lists them against the exception
hierarchy they come from.
