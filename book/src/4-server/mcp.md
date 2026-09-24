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

`/mcp` with no mode is not a fourth, default tier -- there is no default
tier. It has no row in the runner's verb table, so it is refused (`403`)
like any path nobody decided on; a client has to name `read`, `author` or
`operate`.

## Modes

The mode chooses a tier; every tool at or below it is listed, so a client in
`read` mode cannot call a tool that moves anything because it was never told
one exists.

| mode | adds | for |
| --- | --- | --- |
| `read` (default) | every `GET`, plus `check_program` and `check_rig`, which validate and return | asking the rig questions |
| `author` | saving programs, dashboards and tunings to the store | "write me a program that…", "make a dashboard for the blender" |
| `operate` | one tool per device command (`blender-set_humidity`), demands, controllers, running programs, recording, a simulation's knobs, and `stop_rig` (the [software stop](../1-running/runner/access.md#stopping-the-rig)) | driving the rig |

Entering a mode needs a verb (pending D-034): `read` for `/mcp/read`,
`operate` for `/mcp/author` and `/mcp/operate`. Every call a tool makes
then carries the caller's own verbs cut to what the mode allows, so a
`read` token at `/mcp/read` cannot reach a route that needs more, whatever
the tool.

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

Whoever can reach `/mcp` through the door can drive what its mode offers,
so a model needs a credential wherever a person would:

- **the `local` shape** (`flyball run` with nothing configured) needs none,
  on the machine itself;
- **a front with a sign-in** (`password`, `proxy`) takes a named token,
  made on the rig's host: `flyball token create --name claude --kind agent
  --config rig.yaml` for `read`, `--scope operate` to drive. An `agent`
  token lives 30 days at most;
- **a bare runner** takes its own token.

The client sends it as a header:

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

The stdio server takes `--token` or `FLYBALL_TOKEN`. A bare runner with no
token answers only to `localhost`, `127.0.0.1` and `[::1]`, and its MCP
transport checks the same itself (the MCP SDK's DNS rebinding protection:
`Host` and any `Origin` on a loopback name, else `421` / `403`), unless it
was served open on the network by `--insecure-open`, where the door takes
only an IP address, a loopback name or the machine's own name. Behind a
front, or with a token, that check is off and the door's `Host` and `Origin`
rules apply instead ([Authentication](api.md#authentication)).

Every tool call is recorded like any other request that needs more than
`read`: in the runner's audit, as the caller, `via: mcp`.

## What the model sees

- A device command's tool carries the argument schema the rig publishes:
  titles, units, and this instance's limits, so an out-of-range argument is
  refused before it is sent. A command that interrupts a controller is
  marked destructive, so a client can ask first; its description says the
  controller goes to manual once the command succeeds, and the call's result
  is `{result, interrupted: [{controller, was}]}`, naming it. A command with
  a `mode` or `writes` that does not interrupt says it is refused while a
  controller drives the device.
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
- `activities` lists what a running program is waiting on; `fire_activity`
  answers one (the operator pressed the button, or wants a timer skipped)
  and `cancel_activity` ends the program at that step, `cancelled`;
  `cancel_program` cancels a running program, and `run_program`'s `cancel`
  cancels one before starting another. `sim_advance` advances a stepped
  clock. The program step
  `prompt` -- the operator-answered activity -- is unrelated to an MCP
  prompt; nothing here uses MCP's own prompts feature.
- Streams have no equivalent: `read`, `read_many` and `events` are what a
  model polls. On `read` and `author` they answer from the latest poll only;
  `operate` has the same two tools with a `fresh` argument for a live device
  read, so the read tier's "nothing here changes the rig" stays true.
  A reading with no value comes back as `value: null` with its `quality`
  (`invalid`, `stale`, `not_applicable`), `reason`, `last_usable` and
  `age_s` ([no value](wire.md#a-reading-with-no-value)): a model never
  sees a number that is not one.
  `session_series` is one recorded signal over a session; `session_ticks`
  is a recorded controller's steps over one -- mode, correction and, when
  logged, setpoint, output and measured, the data behind a ramp's setpoint
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
  runner's `--drivers` directory again so the type can be attached;
  `search_drivers` searches a `linux/` checkout's hardware catalogue by
  part, category, interface, unit or domain (it runs that checkout's
  search script, so it is drive-tier like `check_driver`). Both run a file
  the caller names on the machine the server runs on, so they are served
  by the stdio server (`flyball-mcp`) only, never over HTTP;
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
