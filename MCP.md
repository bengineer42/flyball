# MCP: state and what remains

The MCP surface lives in `controller/src/flyball/mcp/` and is built from the
daemon's HTTP API; it imports nothing but `flyball.client` and
`flyball.scaffold`. A tool that needs a route the daemon does not serve yet
is declared with `route=(method, path)` and is **not listed** until the
daemon's `/openapi.json` shows that route -- so the server side can land in
any order and the tools appear as it does. The book page is
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

## Needed outside `flyball/mcp/` (tools already written; listed once the route exists)

| tool | route | what it needs |
|---|---|---|
| `list_drivers` | `GET /api/drivers` | every registered `DriverConfig` tag with `model_json_schema()` and the module it came from (`Config.registry`, filtered to `DriverConfig`) |
| `reload_drivers` | `POST /api/drivers/reload` | a `--drivers DIR` option on `flyball-daemon` (default `drivers/` beside the first rig file, like `programs/`); import every `*.py` in it at start and on this route, `importlib.reload` for one seen before; return what registered and any import error per file |
| `probe_hardware` | `GET /api/probe?scan=` | `flyball_linux.probe.report(scan)` when `flyball_linux` is importable, else 404; `scan` does the I²C scan |
| `link_query` | `POST /api/links/{name}/query` `{text}` → `{reply}` | one `query(text)` on a `TextLink` the rig holds (409 for a link that is not text); the raw way to identify an instrument before writing its entry |

Also, not gated because nothing on the wire changes:

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
