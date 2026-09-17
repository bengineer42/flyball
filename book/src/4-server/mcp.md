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
nor a password on the runner there is no authentication at all: anyone who
can reach the port can drive the rig, over `/api` as much as over `/mcp`.
Put a token on any runner a model can drive; the read tier is what
`auth.anonymous: read` lets through without one.

## What the model sees

- A device command's tool carries the argument schema the rig publishes:
  titles, units, and this instance's limits, so an out-of-range argument is
  refused before it is sent. A command that interrupts a controller is
  marked destructive, so a client can ask first.
- `describe_device` is the schema; `widget_schema` every dashboard widget
  kind with its `config`; `program_schema` the program dialect. Together
  they are what a model needs to write a program or a dashboard that names
  real signals, and `check_program` / `save_dashboard` say what it named
  that the rig lacks.
- `update_dashboard` changes a dashboard by its parts (add, remove, move,
  reconfigure a widget) rather than by rewriting the document. Programs are
  text and are saved whole.
- Streams have no equivalent: `read`, `read_many` and `events` are what a
  model polls.
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
  `probe_hardware` says what buses the board has and `link_query` sends
  one raw command down a link, to find out what an instrument is before
  writing its entry. Most instruments need no code: the `scpi` and
  `modbus` drivers take their signals from the rig-file entry, and the
  guide says so first.
- A tool that needs a runner route is listed only while the runner serves
  it (from `/openapi.json`), so an older runner shows fewer tools rather
  than broken ones.

Three routes exist for this and the CLI: `GET /api/rig/schema`, `GET
/api/rig/config` and `POST /api/rig/check`, so a rig file can be checked
against the drivers the runner has without installing them where the model
runs. `GET /api/dashboards/widgets` is the widget catalogue -- a copy of the
UI's registry kept beside the server, since the kinds are the UI's.
