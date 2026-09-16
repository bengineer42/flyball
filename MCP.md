# MCP: state and what remains

The MCP surface lives in `controller/src/flyball/mcp/` and is built from the
daemon's HTTP API; it imports nothing but `flyball.client` and
`flyball.scaffold`. A tool that needs a particular daemon route is declared
with `route=(method, path)` and is listed only while the daemon's
`/openapi.json` shows that route, so an older daemon lists fewer tools
rather than broken ones. The book page is
`book/src/3-running/mcp.md`.

## Done (16 Sep 2026)

- `flyball-mcp` over stdio and the same servers mounted in the daemon at
  `/mcp/read`, `/mcp/author`, `/mcp/operate`; `.mcp.json` at the repo root.
- Read / author / drive tiers; one tool per device command from `/api/schema`.
- Programs, dashboards (with `update_dashboard` by parts), tunings, sessions.
- Rig file: `rig_schema`, `rig_config`, `check_rig` (`GET /api/rig/schema`,
  `GET /api/rig/config`, `POST /api/rig/check`).
- Composition, over the routes in `server/routes/composition.py`:
  `attach_link` / `detach_link`, `attach_device` / `detach_device`,
  `attach_document`, `rig_document`, `rig_changes`, `rig_versions`,
  `rig_version`, `restore_rig_version`, `save_rig`. A tool that changes the
  rig rebuilds the tool list and sends `notifications/tools/list_changed`.
- Driver authoring: `driver_guide` (also the resource
  `flyball://guide/driver`), `driver_scaffold`, `check_driver` (imports a
  module in a fresh interpreter where the server runs and reports tags,
  schemas, descriptors, commands).
- Widget catalogue `GET /api/dashboards/widgets` from
  `controller/src/flyball/server/widgets.json`.

- Drivers, over `server/routes/drivers.py` (16 Sep 2026, later the same
  day): `list_drivers`, `reload_drivers` (the daemon's `--drivers DIR`,
  default `drivers/` beside the first rig file), `probe_hardware`,
  `link_query`. Verified end to end on the humidity sim: `driver_scaffold`
  → file in the drivers dir → `check_driver` → `reload_drivers` →
  `attach_device` → the new device's command is a tool → `read` its
  signal.

- Auth: `flyball-daemon --token` / `FLYBALL_TOKEN`; one bearer token for
  `/api`, `/ws` (`?token=` allowed) and `/mcp`. Client, CLI and
  `flyball-mcp` send it. No token: open, as before.
- Hot-attach on hardware is the daemon's `--compose` opt-in (server side
  by the other session); the attach tools say so.

## Still open

- `widgets.json` is now generated: `npm run widgets-json` in `ui/` (done,
  other session). Run it after a widget change.
- **UI**: token entry and headers; a "Connect a model" card with the three
  `/mcp/<mode>` URLs, the `claude mcp add --transport http …` one-liner
  and the token (other session).
- The `x-signal` / `unit` keys in device-command schemas are passed
  through untouched; accepted by the Python SDK client, unverified against
  other hosts. Strip them in `_device_tools` if a host rejects the list.

## Conventions the tools assume

- Device command tools are `<device>-<command>`; hyphen because tool names
  cannot contain dots and identifiers cannot contain hyphens.
- A command with `simulation=True` is listed only when `/api/sim` says
  `simulated: true`; `sim_*` tools likewise.
- A tool marked `changes_tools` (attach/detach/document/restore/reload)
  refetches `/api/schema` off the event loop and announces the change.
- `check_driver` runs the file's top level in a subprocess on the host the
  server runs on (the daemon host when mounted). It is drive tier for that
  reason.
