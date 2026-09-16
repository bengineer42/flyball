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

## Still open

- **`widgets.json` is hand-maintained** from `ui/apps/dashboard/src/widgets/*.tsx`.
  A generator on the UI side (evaluate each kind's `configSchema` with
  empty bindings, write the JSON) would stop it drifting; until then a
  widget change means editing the JSON.
- **Auth**: `/mcp/*` has the same absence of authentication as `/api/*`. A
  bearer token on both is the natural next step once a model can drive
  hardware; the SDK's `streamable_http_app(auth=…, token_verifier=…)`
  takes one, or the daemon's own middleware can front both.
- **Hot-attaching on hardware**: `attach_device` works on a hardware rig as
  it does on a simulation. Whether that wants an explicit opt-in is an open
  question (a device that owns a real PWM channel, added while a controller
  runs).
- **UI "Connect a model" card**: the three `/mcp/<mode>` URLs and the
  `claude mcp add --transport http …` one-liner, on a settings or rig page.

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
