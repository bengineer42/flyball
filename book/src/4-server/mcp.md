# The MCP server

The runner puts a running rig in front of a model: Claude Code, Claude
Desktop, or any MCP client. It serves MCP over HTTP at `/mcp/read`,
`/mcp/author` and `/mcp/operate`, so connecting is a URL and nothing to
install where the model runs:

```
claude mcp add --transport http rig http://pi:8000/mcp/author
```

Like the [CLI](../1-running/cli/index.md) the tools are built from the rig's schema and know
nothing about any particular device. The same server runs over stdio for a
machine that can reach the rig but is not it (the `mcp` extra):

```
flyball-mcp --url http://pi:8000 --mode author     # or export FLYBALL_URL
```

The repository's `.mcp.json` points Claude Code at a local runner's
`/mcp/author`, so a checkout gets the simulated rig offered on first open.

`POST /mcp` (no mode) is not a fourth, default tier -- there is no default
tier. Reached through the daemon, it 307-redirects to `/mcp/` (Go's
`net/http.ServeMux` adding the trailing slash its `/mcp/` registration
expects), which is still not a mode and so still answers nothing; a client
has to name `read`, `author` or `operate`.

## Modes

The mode chooses a tier; every tool at or below it is listed, so a client in
`read` mode cannot call a tool that moves anything because it was never told
one exists.

| mode | adds | for |
| --- | --- | --- |
| `read` (default) | every `GET`, plus `check_program` and `check_rig`, which validate and return | asking the rig questions |
| `author` | saving programs, dashboards and tunings to the store | "write me a program that…", "make a dashboard for the blender" |
| `operate` | one tool per device command (`blender-set_humidity`), demands, controllers, running programs, recording, a simulation's knobs | driving the rig |

Two entries in the client's config, one per mode you want, is the usual
arrangement:

```json
{
  "mcpServers": {
    "rig": {"type": "http", "url": "http://pi:8000/mcp/author"},
    "rig-operate": {"type": "http", "url": "http://pi:8000/mcp/operate"}
  }
}
```

## The token

A runner with a token (`--token`, `FLYBALL_TOKEN`, `auth.token`) requires it
on everything it serves -- `/api`, `/ws` and `/mcp` alike, since any of
them can drive the rig. A model cannot type a password at a login page,
so a runner that has only a password needs a token too before a client
outside it can connect (its own mount at `/mcp` keeps working). An MCP
client sends the token as a header:

```json
{
  "mcpServers": {
    "rig": {
      "type": "http",
      "url": "http://pi:8000/mcp/operate",
      "headers": {"Authorization": "Bearer <token>"}
    }
  }
}
```

The stdio server takes `--token` or `FLYBALL_TOKEN`. With neither a token
nor a password on the runner there is no authentication at all: anyone on
the runner's own machine can drive the rig, over `/api` as much as over
`/mcp`. Such an open runner answers only to `localhost`, `127.0.0.1` and
`[::1]`, and its MCP transport checks the same itself (the MCP SDK's DNS
rebinding protection: `Host` and any `Origin` on a loopback name, else
`421` / `403`), so `http://localhost:8000/mcp/author` works and
`http://pi:8000/mcp/author` needs the token. On a runner with a door the
transport's check is off -- the runner does not know every name it is
reached by -- and the door refuses a foreign `Origin` instead (see
[Authentication](api.md#authentication)). Put a token on any runner a
model can drive; the read tier is what `auth.anonymous: read` lets
through without one.

## What the model sees

- A device command's tool carries the argument schema the rig publishes:
  titles, units, and this instance's limits, so an out-of-range argument is
  refused before it is sent. A command that interrupts a controller is
  marked destructive, so a client can ask first.
- `list_devices` is name, type, label and a one-line description -- not the
  full tree `GET /api/devices` answers (signals, commands, conditions),
  which is tens of kB even on a one-device rig; its `detail` argument asks
  for that instead. `describe_device` for one device's full schema either
  way. A tool that answers a list wraps it in a named key (`{"devices":
  [...]}`, `{"controllers": [...]}`, and so on), not a bare array, and
  declares that shape as its output schema.
- A name, address or id the model passes goes into the route's path as
  exactly one segment, percent-encoded (so a `?` or `#` in it cannot add a
  query or cut the path short); an empty one, `.`, `..` or one holding a
  `/` is refused with a tool error before anything is sent, so no argument
  can reach a route other than the tool's own. The Python client does the
  same (`flyball.interfaces.client.segment`).
- `describe_device` is the schema; `widget_schema` every dashboard widget
  kind with its `config`; `program_schema` the program dialect. Together
  they are what a model needs to write a program or a dashboard that names
  real signals, and `check_program` / `save_dashboard` say what it named
  that the rig lacks.
- `update_dashboard` changes a dashboard by its parts (add, remove, move,
  reconfigure a widget) rather than by rewriting the document. Programs are
  text and are saved whole.
- Streams have no equivalent: `read`, `read_many` and `events` are what a
  model polls. On `read` and `author` they answer from the latest poll only;
  `operate` has the same two tools with a `fresh` argument for a live device
  read, so the read tier's "nothing here changes the rig" stays true.
  `session_series` is one recorded signal over a session; `session_ticks`
  is a recorded controller's steps over one -- mode, correction and, when
  logged, setpoint, demand and reading, the data behind a ramp's setpoint
  curve, which no signal series carries.
- The rig can be built up: `attach_link`, `attach_device`, or a whole
  document with `attach_document`; `rig_document` shows the result,
  `rig_versions` every change, `restore_rig_version` undoes one, `save_rig`
  writes it out. A change rebuilds the tool list, so a new device's
  commands appear as tools at once. A simulated or bare rig can always be
  built up; a hardware rig only when the runner runs with `--compose`.
- New equipment: `driver_guide` (also the resource `flyball://guide/driver`)
  says how to write a driver and when not to; `driver_scaffold` gives a
  module that already runs; `check_driver` imports one where the server
  runs and reports what it registers; `reload_drivers` imports the
  runner's `--drivers` directory again so the tag can be attached;
  `search_drivers` searches a `linux/` checkout's hardware catalogue by
  part, category, interface, unit or domain (it runs that checkout's
  search script, so it is drive-tier like `check_driver`);
  `probe_hardware` says what buses the board has and `link_query` sends
  one raw command down a link, to find out what an instrument is before
  writing its entry. `probe_hardware` (`POST /api/probe`) is read-tier for
  the board and bus list; `operate` has the same tool with a `scan` argument for the
  addresses answering on each I2C bus, a bus transaction some devices
  mind, so it is not offered below that tier. Most instruments need no
  code: the `scpi` and `modbus` drivers take their signals from the
  rig-file entry, and the guide says so first.
- A tool that needs a runner route is listed only while the runner serves
  it (from `/openapi.json`), so an older runner shows fewer tools rather
  than broken ones.

Three routes exist for this and the CLI: `GET /api/rig/schema`, `GET
/api/rig/config` and `POST /api/rig/check`, so a rig file can be checked
against the drivers the runner has without installing them where the model
runs. `GET /api/dashboards/widgets` is the widget catalogue -- a copy of the
UI's registry kept beside the server, since the kinds are the UI's.
