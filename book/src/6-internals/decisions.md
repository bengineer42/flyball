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
| **D-010** | Authentication: a password for people, traded at the UI's login page for a signed `HttpOnly` cookie (no session table; the key beside the store; a changed password signs everyone out), the bearer token kept for machines, `scrypt` in the file, `anonymous: none | read` for a rig the public may watch; one principal with a level per request and one comparison, so several sign-ins or a locked part of the rig later change who gets a level, not the routes | decided; where it lives superseded by D-032: the bare runner keeps only the token and a one-time link, and people sign in at the front |
| **D-012** | The daemon's own auth: one bearer token (`auth.token` in `flyballd.yaml`, `FLYBALLD_TOKEN` on the CLI) on every route of the daemon's own -- registration, and since 23 Sep the runner list and landing page too (the per-runner proxy is the runner's door, not the daemon's) -- and **closed until it is set** -- no token means 503, not open; manifests validated (`name`, `root_path`) once before either becomes a path or a line of HTML | decided; replaced in the auth work (D-032): the daemon's management routes need a named token with the `manage` scope, and `auth.token` in `flyballd.yaml` is gone |
| **D-013** | Extensions restructure: `engine/` stays pure core plus hardware protocols only, `sim/` is new (zero third-party deps, pulled in by `flyball[web]`), `extensions/` holds every dependency-gated package (`linux`, `chips`, `modbus`, `visa`, `bluesky`, `qcodes`, `pymeasure`), `examples/furnace/` is the one worked `MultiPlant` scenario moved out | decided |
| **D-014** | Engine restructure: `core/` split into `foundation/{device,time,router,config,quantities}/`; six implicit global registries (`Config.registry`, `ControlLaws`, `Feedforwards`, `SetPointGenerators`, and `sequencing.command.Commands`) replaced by an explicit `Catalog`/`Config`/`Instance` system in `model/`, now wired into the real build path (`flyball-runner` builds one `Catalogs`, `discover()`s it, and every consumer -- `DeviceEntry.build`, `RigConfig.model_validate`/`model_json_schema`, `/api/drivers`, `/api/drivers/reload`, program-step parsing -- reads from it, not a bare `ClassVar` dict); `rig/`, `library/`, `record/` (renamed from `db/`), `interfaces/` (`server`+`mcp`+`client` grouped), `sequencing/` (renamed from `programmer/`) complete the six-layer reorg | decided |
| **D-015** | Dashboards: no front-end stack change (the document / kind-registry / schema-form / same-renderer shape is already the one page builders use); widgets gain view × binding × shape -- a shape computed from the signal's metadata, a `view` with `auto` resolved by one `defaultViewFor(shape)` table shared by the generated Overview, the gauge and the pickers, `accepts(shape)` per kind, a by-signal tab in the Add drawer; per-breakpoint stored layouts pending | decided, not yet built |
| **D-027** | Store errors are classified three ways: a constraint violation is a 409, a store that cannot be reached a 503, anything else stays a 500 because it is a bug; the classification lives in the store's two connection helpers and the existing error map finds it through the class hierarchy | decided |
| **D-028** | An open runner is never served beyond loopback, and an auth misconfiguration removes exposure, never operation: an open runner asked for a network address still runs the rig but binds `127.0.0.1` (same port) and says so on stderr and in `/api/auth` / `/api/health`; `flyball run --serve-ui` does the same for its UI, `flyballd` answers 503 for such a runner. The opt-in is per run only (`--insecure-open`, `FLYBALL_INSECURE_OPEN=1`), never a rig-file key; the dashboard shows a permanent banner while open on the network. Overwriting a rig file keeps its `runner` section | decided |
| **D-029** | Store work never runs on the event loop: store routes are plain `def` behind four slots, and a session delete or trim goes in batches with the lock released between, marked so a cut-off delete is visible and finished at startup -- atomicity of a delete is given up | decided |
| **D-030** | A limit that follows a signal with no value yet -- or a non-finite one -- fails closed: a demand is refused (503) and a controller is held with `limit_unknown`/`limit_known` events. A NaN bound counts as not known, since it silently drops that side of the clamp (`min(max(-50, nan), 100)` is `-50`); refusing rather than clamping to the known end is deliberate, as the unknown end is usually the one protecting the hardware | decided |
| **D-031** | Licence: **MIT**, with a copy in every package root (PEP 639 forbids `..` in `license-files`, so one file at the repository root cannot reach the ten Python packages or the npm ones). Apache-2.0 was recommended first and withdrawn: its patent grant covers only *contributors'* patents, which for a solo author with none is an empty set, and the instrumentation field's own publishers ship MIT (National Instruments' `nidaqmx-python`, Microsoft's QCoDeS, Zurich Instruments' `zhinst-toolkit`). MIT keeps driver code flowing both ways with the MIT/BSD projects flyball already adapts to. Contributions are certified by a **DCO** sign-off rather than a CLA; a CLA would additionally allow relicensing others' work later, and can be added the day that matters — but only for contributions from that day on | decided |
| **D-032** | Authentication has one door for people: a Go **front** (one package, used by `flyball run` and `flyballd`) authenticates people and machines, and the runner authorises from its own route table. The front signs a per-request principal (HMAC-SHA256, versioned, 60 s, a key per runner incarnation) carrying the caller's `sub`, `sid`, scopes, `kind` and `aud`, over a unix socket in a 0700 front-dir. No user accounts yet: one admin password, named tokens, anonymous read. The `local` shape stays sign-in-free on loopback, guarded by `Host` and `Origin` checks. `flyball run` always starts the front. A stop slot (`POST /api/rig/stop`, a `SIGUSR1` break-glass), and revoking a principal never changes hardware state; what a stop does to outputs is the signals work's. Easy wrapping in external identity proxies (trusted-header and signed-JWT presets) | decided |
| **D-033** | flyball terminates TLS from a certificate file: an optional `tls: {cert, key}` on the front, reloaded on renewal; no ACME, no self-signed generation, and a proxy in front stays supported -- a narrow reversal of "flyball does not do TLS" | decided |
| **D-034** | The permission vocabulary: which verbs exist (a ladder such as `read < operate < configure < admin`, or an unordered set with `author`), where the risky routes sit, the MCP modes' verbs, an agent deny-list, and the role names for proxy grants. One point decided: stopping the rig needs `operate`. Until then a two-verb placeholder, `read` and `operate`, reproduces the earlier rule | **open** |
| **D-035** | Distribution: no Go binary in the Python wheel (release binaries later), and no split of the `flyball` distribution yet -- one package with extras; the `web` extra is renamed `server` | decided |
| **D-036** | Named-token lifetimes are configurable, tighten-only (`tokens.default_lifetime`, `tokens.max_lifetime`; built-ins 90 and 365 days; 30 days at most for agent tokens and tokens made over plain HTTP). `flyball login --scope` may ask for more than `read`, with four safeguards: `read` by default and a warning above it; a bare verb means this rig only; at most 30 days above `read`; the token named `cli:<user>@<host>`. `manage` is never issued by a login | decided |
| **D-037** | `flyballd` leaves its runners running when it stops, however it stops, and adopts them when it starts again (the signed handshake, no restart); its systemd unit uses `KillMode=process`. Two explicit stop-alls: `flyball stop --all` (the rig stop on every rig, `operate` on each, processes stay up) and `flyball runners stop --all` (end the processes, `manage`). `flyball run` still stops its runner on Ctrl-C | decided |
| **D-038** | A dropped terminal never stops a `flyball run` rig: the front and the runner ignore the hangup in every mode, their output also goes to a log file, and a notice at start says so; Ctrl-C and SIGTERM still stop it. No adoption for `flyball run`; a rig that must survive reboots belongs under `flyballd` | decided |

Nothing in this book is settled unless `DECISIONS.md` says so. Where a
chapter describes intent rather than fact, it says which.

## Open questions

- **Limits and clamps.** A writable signal now carries `limits`,
  clamped on every demand (D-006) — that closes the "no setpoint bounds"
  half of this. A rate-of-change clamp (`max_rate`) and a hold on a stale
  sensor (`stale_after`) followed ([`signals`](../2-config/devices/index.md#signals)),
  and a limit that follows a signal with no value yet, or a non-finite one,
  now fails closed (D-030): the
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
