# Decisions

Options weighed and chosen. The full records, with context and consequences,
are in `DECISIONS.md` at the repository root; this is the index.

| | decision | status |
| --- | --- | --- |
| **D-001** | Control loop execution model: what sets the cadence, whether stages run in lockstep, what concurrency carries them | open; needs measurement of the plant and the link |
| **D-002** | Serialisation formats: YAML documented for both programs and rig files (deep envelopes read better, and programs are already YAML); TOML still accepted for a rig file in the same shape; JSON on the wire | decided |
| **D-003** | Addressing controllers: which controller a step means is a field with a program-level default — the Python program-step dataclasses still call the field `loop`, though the concept and the class are now `Controller`; coupled segments are two steps that start together | decided, with an escape hatch recorded |
| **D-004** | ~~Readings model: measurand, source, channel, reading, sample; units on the measurand only~~ | superseded by D-006 |
| **D-005** | Front-end framework: React + TypeScript with Vite; `react-jsonschema-form`, uPlot, `react-grid-layout` | decided |
| **D-006** | Device model: quantity (name + unit, not interned), signal (an address, a role and an access set — R/P/W), device (a static tree of signals and commands over one store), controller (one publishing signal regulated through one demand); demands are what controllers drive, commands are what people and programs run, outputs are read — reverses D-004's measurand/source/channel/loop model and its process-wide interning | decided |
| **D-007** | Runner config and a public runner: a `runner:` section in the rig file (or a file that `extends` it) with the process settings, flags overriding; `--root-path` served by the runner itself and a UI that finds its API from where it was served; writes behind `--allow-save` / `--allow-shutdown` / `--no-mcp`, all off by default; rig versions as a tree with a head, a restore moving the head rather than copying | decided |
| **D-008** | The scratch record and retention: a rig nobody is recording still keeps its last hour, in the store (not memory), as a scratch session written *instead of* a recording rather than beside one; trimming moves the session's start against a hidden origin instead of rewriting offsets; a per-session size estimate plus the real file size for the cap; `keep` / `keep_size` / `retain` / `rotate` / `max_store` in the `runner:` section, pinned sessions exempt, the oldest data first under the cap whatever its kind | decided |
| **D-010** | Authentication: a password for people, traded at the UI's login page for a signed `HttpOnly` cookie (no session table; the key beside the store; a changed password signs everyone out), the bearer token kept for machines, `scrypt` in the file, `anonymous: none | read` for a rig the public may watch; one principal with a level per request and one comparison, so several sign-ins or a locked part of the rig later change who gets a level, not the routes | decided |
| **D-012** | The daemon's own auth: one bearer token (`auth.token` in `flyballd.yaml`, `FLYBALLD_TOKEN` on the CLI) on every registration route, and **closed until it is set** -- no token means 503, not open; manifests validated (`name`, `root_path`) once before either becomes a path or a line of HTML | decided |
| **D-013** | Extensions restructure: `engine/` stays pure core plus hardware protocols only, `sim/` is new (zero third-party deps, pulled in by `flyball[web]`), `extensions/` holds every dependency-gated package (`linux`, `chips`, `modbus`, `visa`, `bluesky`, `qcodes`, `pymeasure`), `examples/furnace/` is the one worked `MultiPlant` scenario moved out | decided |
| **D-014** | Engine restructure: `core/` split into `foundation/{device,time,router,config,quantities}/`; six implicit global registries (`Config.registry`, `ControlLaws`, `Feedforwards`, `SetPointGenerators`, and `sequencing.command.Commands`) replaced by an explicit `Catalog`/`Config`/`Instance` system in `model/`, now wired into the real build path (`flyball-runner` builds one `Catalogs`, `discover()`s it, and every consumer -- `DeviceEntry.build`, `RigConfig.model_validate`/`model_json_schema`, `/api/drivers`, `/api/drivers/reload`, program-step parsing -- reads from it, not a bare `ClassVar` dict); `rig/`, `library/`, `record/` (renamed from `db/`), `interfaces/` (`server`+`mcp`+`client` grouped), `sequencing/` (renamed from `programmer/`) complete the six-layer reorg | decided |
| **D-015** | Dashboards: no front-end stack change (the document / kind-registry / schema-form / same-renderer shape is already the one page builders use); widgets gain view × binding × shape -- a shape computed from the signal's metadata, a `view` with `auto` resolved by one `defaultViewFor(shape)` table shared by the generated Overview, the gauge and the pickers, `accepts(shape)` per kind, a by-signal tab in the Add drawer; per-breakpoint stored layouts pending | decided, not yet built |
| **D-027** | Store errors are classified three ways: a constraint violation is a 409, a store that cannot be reached a 503, anything else stays a 500 because it is a bug; the classification lives in the store's two connection helpers and the existing error map finds it through the class hierarchy | decided |
| **D-028** | An open runner is never served beyond loopback, and an auth misconfiguration removes exposure, never operation: an open runner asked for a network address still runs the rig but binds `127.0.0.1` (same port) and says so on stderr and in `/api/auth` / `/api/health`; `flyball run --serve-ui` does the same for its UI, `flyballd` answers 503 for such a runner. The opt-in is per run only (`--insecure-open`, `FLYBALL_INSECURE_OPEN=1`), never a rig-file key; the dashboard shows a permanent banner while open on the network. Overwriting a rig file keeps its `runner` section | decided |
| **D-029** | Store work never runs on the event loop: store routes are plain `def` behind four slots, and a session delete or trim goes in batches with the lock released between, marked so a cut-off delete is visible and finished at startup -- atomicity of a delete is given up | decided |

Nothing in this book is settled unless `DECISIONS.md` says so. Where a
chapter describes intent rather than fact, it says which.

## Open questions

- **Limits and clamps.** A writable signal now carries `limits`,
  clamped on every demand (D-006) — that closes the "no setpoint bounds"
  half of this. A rate-of-change clamp (`max_rate`) and a hold on a stale
  sensor (`stale_after`) followed ([`signals`](../2-config/devices/index.md#signals)),
  and a limit that follows a signal with no value yet now fails closed: the
  demand is refused (503), a controller's write held with a `limit_unknown`
  event — it used to pass unclamped;
  still open: runaway detection.
- **Adaptation in control.** Estimator and retune policy exist; wiring them
  into a controller is not done.
- **Model-based control.** MPC is the natural home for limits and would take
  the identified model directly. Not started.

Resolved since the last pass, and dropped from this list: **multi-loop** —
a controller is addressable, carries its own source and mode, and the rig
now hosts as many as have targets (`Controllers`, keyed by the target's
address, with a `default`). **Events** — `core.device.Event`, `rig.events`
and `rig.recent` exist; the programmer records step outcomes through
`rig.event(...)`; a session records them (`SessionWriter.write_event`).
**Per-driver registries** — every `flyball.configs`-registering package
(`extensions/{linux,chips,modbus,visa,bluesky,qcodes,pymeasure}`, `sim`,
`examples/{furnace,humidity}`, and engine's own built-in laws, feedforwards
and generators) now has an explicit `register(catalog)`, called by
`Catalogs.discover()`; the four old implicit, process-wide
`__init_subclass__` writes (`Config.registry`, `ControlLaws`,
`Feedforwards`, `SetPointGenerators`) are all gone, so a collision is caught
by the `Catalog` that actually holds a tag (`register()` raises), not
silently, for devices and links *and* laws/feedforwards/generators alike.
`discover()` still only runs at runner startup (live reload of a
newly-installed package remains open, unlike a `drivers/` directory's `POST
/api/drivers/reload`), but a missing or silently-empty `register()` is no
longer untested: `engine/tests/test_catalog_discovery.py` fails the suite if
any installed entry point doesn't register something, across all five
kinds. One asymmetry remains, not a registry gap: a rig *file*'s `law`/
`feedforward`/`generator` fields are still typed from engine's built-ins
directly (`interfaces/server/schemas.py`, `runtime/config.py`), not
`get_catalog()` -- unlike a device driver or link, nothing outside engine
defines one of these today, and building that union from `Catalogs
.discover()` at those modules' own import time risks `discover()`
re-entering a module still mid-import through an extension's own import
chain (`flyball_sim` imports `RigConfig` from `runtime/config.py`, for
instance). A third-party law would need that static typing revisited, the
same way `rig_model()` already rebuilds the `links` field dynamically per
request for devices/links.
