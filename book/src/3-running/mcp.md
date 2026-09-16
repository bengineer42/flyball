# The MCP server

The daemon puts a running rig in front of a model: Claude Code, Claude
Desktop, or any MCP client. It serves MCP over HTTP at `/mcp/read`,
`/mcp/author` and `/mcp/operate`, so connecting is a URL and nothing to
install where the model runs:

```
claude mcp add --transport http rig http://pi:8000/mcp/author
```

Like the [CLI](cli.md) the tools are built from the rig's schema and know
nothing about any particular device. The same server runs over stdio for a
machine that can reach the rig but is not it (the `mcp` extra):

```
flyball-mcp --url http://pi:8000 --mode author     # or export FLYBALL_URL
```

The repository's `.mcp.json` points Claude Code at a local daemon's
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

The daemon has no authentication, for `/mcp` as for `/api`: anyone who can
reach the port can drive the rig either way.

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

Three routes exist for this and the CLI: `GET /api/rig/schema`, `GET
/api/rig/config` and `POST /api/rig/check`, so a rig file can be checked
against the drivers the daemon has without installing them where the model
runs. `GET /api/dashboards/widgets` is the widget catalogue -- a copy of the
UI's registry kept beside the server, since the kinds are the UI's.
