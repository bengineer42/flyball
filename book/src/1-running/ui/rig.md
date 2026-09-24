# The rig file

Two tabs of Options, behind the gear in the app bar: **Rig file** (`#/options/rig`; the old `#/rig` address opens it) is what the rig is made of -- devices, links, controllers, the running document and what changed since start; **Runner** (`#/options/runner`) is the rig as a whole -- its versions and restore, saving it, restarting or shutting down the runner, and connecting a model from outside the browser. The list below covers both. The file it mirrors: [Configuration](../../2-config/index.md); the routes behind each box: [Composition](../../4-server/api.md#composition).

!!! tip "At the terminal"
    `flyball rig check FILE…` validates a file without a runner; `flyball sim …` drives a simulated rig's knobs; save, versions, restart and shut down are routes for now -- [The rig and the runner](../cli/rig.md).

## Rig file and Runner

What each box shows:

- **Running document** (`GET /api/rig/document`) — links, devices and
  controllers as they are now, as read-only YAML.
- **Changed since start** (`GET /api/rig/changes`) — an overlay of what
  differs from the files the runner loaded (a key removed appears as
  `null`), highlighted once it is non-empty.
- **Versions** (`GET /api/rig/versions`) — every version the store has
  seen, newest first, the one the running rig is at marked **current**
  (its restore is disabled) and each row saying which version it was made
  from; **restore** (`POST /api/rig/versions/{id}/restore`) saves that
  version again as a new one on top and restarts the rig with it.
- **Save** (`POST /api/rig/save`) — with no path, just what changed since
  start and was not saved yet (a controller attached or detached: every
  other change saved itself), written to the overlay beside the file the rig was loaded from; a
  path writes the whole rig there instead, with a checkbox to overwrite a
  loaded file. The path field only appears on a runner that allows it
  (`allow_save`); otherwise the box says so and saves the overlay alone.
- The section head shows where the runner serves from and how many files
  it loaded; on a runner started with `allow_shutdown`, **Restart** and
  **Shut down** buttons beside it, each behind a confirmation. Restart
  runs the same command again: the rig is rebuilt from its files and the
  overlay the changes were saved to, the app reconnects within a few
  seconds.
- **Every add, remove and restore restarts the rig** (D-051): it is saved
  as a new version, the rig is stopped -- outputs to their stop states, a
  running program cancelled, controllers to manual -- and the runner comes
  back on the new version, passive, within a few seconds. Nothing is added
  or removed in place. See
  [Building a rig while it runs](../runner/building.md).
  In the app, each one asks first and says what applying does. Adding asks
  once; removing a link or device, and restoring a version, also asks you to
  type its name (or the version's number). With a program running, the edit
  is refused and the app offers to cancel the program and apply it anyway.
  While the runner restarts, a banner at the top of the page says so; once
  it is back the app reconnects, every page reads the new rig, and the
  banner says which version it is on. If the new version could not be built,
  the rig goes back to the one before and the banner says why.
- **Devices, links and controllers** — Config is the only place in the app to add or remove
  any of the three (Readings, a device's own page and Controllers show them but no longer offer
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
