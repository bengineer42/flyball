# The rig file

**Options › Rig file** (`#/options/rig`, behind the gear in the app bar; the old `#/rig` address opens it) is the running rig as a file would show it, its history, and how to reach the runner from outside the browser. The file it mirrors: [Configuration](../../2-config/index.md); the routes behind each box: [Composition](../../4-server/api.md#composition).

!!! tip "At the terminal"
    `flyball rig check FILE…` validates a file without a runner; `flyball sim …` drives a simulated rig's knobs; save, versions, restart and shut down are routes for now -- [The rig and the runner](../cli/rig.md).

## Config

The **Rig file** tab (`#/options/rig`) is the running rig as a file would show it, alongside its
history and how to reach it from outside the browser:

- **Running document** (`GET /api/rig/document`) — links, devices and
  controllers as they are now, as read-only YAML.
- **Changed since start** (`GET /api/rig/changes`) — an overlay of what
  differs from the files the runner loaded (a key removed appears as
  `null`), highlighted once it is non-empty.
- **Versions** (`GET /api/rig/versions`) — every version the store has
  seen, newest first, the one the running rig is at marked **current**
  (its restore is disabled) and each row saying which version it was made
  from; **restore** (`POST /api/rig/versions/{id}/restore`) rebuilds the
  running rig to match that version and moves the head there.
- **Save** (`POST /api/rig/save`) — with no path, just what changed since
  start, written to an overlay beside the file the rig was loaded from; a
  path writes the whole rig there instead, with a checkbox to overwrite a
  loaded file. The path field only appears on a runner that allows it
  (`allow_save`); otherwise the box says so and saves the overlay alone.
- The section head shows where the runner serves from and how many files
  it loaded; on a runner started with `allow_shutdown`, **Restart** and
  **Shut down** buttons beside it, each behind a confirmation. Restart
  runs the same command again: the rig is rebuilt from its files, the app
  reconnects within a few seconds.
- **Devices, links and controllers** — Config is the only place in the app to add or remove
  any of the three (the Devices, Inputs and Controllers pages show them but no longer offer
  add/remove for anything but a controller's own detach). Each section lists what exists as
  chips (a device's name and driver, a link's name and type, a controller's target and
  source), each with a remove button behind a confirmation; an **Add** button opens the same
  dialog the Devices/Controllers pages used before this moved here — see [Devices](devices.md)
  and [Controllers](controllers.md) for what each dialog asks.
- **Connect a model** — this runner's [MCP](../../4-server/mcp.md) server, one tier per
  mode: each row is that tier's absolute URL, a ready-made
  `claude mcp add --transport http …` line, and (below all three) a client
  config block naming all of them, one copy button each. The config's
  `headers` carry a `Bearer <token>` placeholder wherever the door is not
  the `local` shape -- the app never holds one. Behind a `password` or
  `proxy` front, put a named token there (`flyball token create`,
  [The MCP server](../../4-server/mcp.md#the-token)); at a bare runner, its
  own token. At the `local` shape the block carries no headers.
