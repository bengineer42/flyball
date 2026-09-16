# Stress rigs

Config files that push the daemon and the UI harder than any real rig would,
plus the ordinary two-loop and reverse-acting examples that belong in
[`../simulated/`](../simulated/README.md) instead. Nothing here is Python:
every file is `sim_*`/`fake_*` links read by the schema, routes, CLI and UI
exactly as a real bench would be.

| file | what it stresses | scale |
| --- | --- | --- |
| `plant.yaml` | "the works": everything at once | 41 published signals, 20 units, 17 controllers (P/PI/PID × none/affine/table), 3 writable devices with no controller, poll periods spread 0.2–5 s |
| `torrent.yaml` | throughput | 8 signals at 0.02–0.05 s, clock ×40 → **~11,600 samples/s measured** (§ below) |
| `zoo.yaml` | breadth of unit | 21 signals, one per unit the registry renders, 4 with a controller |
| `sparse.yaml` | the empty states | 1 device, 1 signal, nothing writable, no controllers, no programs |
| `bare.yaml` | the *really* empty state | loads with nothing declared at all (see note below) |
| `chaos.yaml` | bad control | small/fast/coupled/laggy/noisy furnace; one law that winds up into the millions of watts, one heater clamped to 40 % of its power, one open-loop zone |
| `longrun.yaml` | history at scale | clock ×600: ten hours of rig time in about a minute of wall time |

Two ordinary examples live in `../simulated/` instead and are documented in
that README: `chiller.yaml` (reverse-acting: negative plant gain) and
`dual.yaml` (two independent controllers, two units, one rig).

Every file is `links:` (the `sim_*`/`fake_*` plants and transports),
`devices:` (`sim_daq` reading plant ports as `[RP]` signals, `sim_drive`
driving them from `[W]` signals, `scpi`/`modbus` over the fakes) and
`controllers:` keyed by the target signal's address -- the shape
`../simulated/README.md` describes. A furnace is one `furnace` daq and one
`heaters` drive on the `tube` link, so its addresses are `furnace.zoneN`
and `heaters.heaterN`; a bare plant's drive is `<name>.drive`, a fraction
of full, and its controller carries the plant's static inverse as an
`affine` feedforward.

## Running one

```bash
cd controller
uv run flyball-daemon ../examples/stress/plant.yaml --record
uv run flyball program run ../examples/stress/programs/plant-firing.yaml
uv run flyball rig check ../examples/stress/chaos.yaml
```

Every stress rig has at least one program in `programs/`, except `sparse.yaml`
and `bare.yaml`, which deliberately have none. `chaos.yaml`'s mid-run
disturbance (failing a thermocouple, kicking a heater) is expressed directly
in `chaos-run.yaml` with `command` steps on the `furnace` and `heaters`
devices (`fail`/`restore` with a `signal`, `disturb` with a `signal` and an
`offset`) and a `hold` whose own `timeout` gives up before its `duration`, to
exercise the "timed out" outcome — see
[`programs.md`](../../book/src/3-running/programs.md#the-commands-every-rig-has)
for both. Run it on its own:

```bash
uv run flyball program run ../examples/stress/programs/chaos-run.yaml
```

`scripts/chaos-disturb.sh` predates the `command` step and drives the same
routes from outside the program instead; kept for reference, but
`chaos-run.yaml` no longer needs it run alongside.

## `bare.yaml`

It loads. `RigConfig` requires no `links`, `devices` or `controllers` — a
rig file can be just a name — so `bare.yaml` is kept rather than dropped: it
is the one file that puts every list in the UI (Inputs, Controllers,
Dashboards, Programs) into its empty state simultaneously.

## `torrent.yaml`: measured sample rate

Measured on this machine on 16 Sep 2026, by starting the daemon with
`--record` and reading the recorder's `reading` row count from the SQLite
store ten seconds apart (no `ws` package was available under
`ui/node_modules` to count `/ws/samples` messages directly, so this is the
alternative the brief allows):

```
count at t:      215,665
count at t+10.0s: 331,441
rate: (331441 - 215665) / 10.0 ≈ 11,568 samples/s
```

Comfortably over the 1000/s target — the theoretical figure from the file's
periods and ×40 clock is ~11,700/s, so the daemon is keeping up with almost
no overhead from Python/threading at this rate on this machine.

## Measured on this machine, 16 Sep 2026

Screenshots at 1440×900 via `$S/tools/shot.mjs`; console counts are that
tool's error/warning tally (see note on the WebSocket/404 lines below, which
are constant across every rig including `bare.toml` and are not caused by
rig content). CPU is `top -b -n1 -p <pid>` on the `flyball-daemon` process
(not the `uv run` wrapper) a few seconds after start.

| rig | page | screenshot | console errors | console warnings |
| --- | --- | --- | --- | --- |
| plant | Overview | `shots/rigs-plant-overview.png` | 8 | 4 |
| plant | Sources | `shots/rigs-plant-sources.png` | 8 | 3 |
| plant | Loops | `shots/rigs-plant-loops.png` | 8 | 4 |
| plant | Dashboards | `shots/rigs-plant-dashboards.png` | 14 | 4 |
| torrent | Overview | `shots/rigs-torrent-overview.png` | 8 | 4 |
| torrent | Sources | `shots/rigs-torrent-sources.png` | 8 | 3 |
| torrent | Loops | `shots/rigs-torrent-loops.png` | 8 | 4 |
| torrent | Dashboards | `shots/rigs-torrent-dashboards.png` | 9 | 4 |
| zoo | Overview | `shots/rigs-zoo-overview.png` | 7 | 4 |
| zoo | Sources | `shots/rigs-zoo-sources.png` | 7 | 3 |
| zoo | Loops | `shots/rigs-zoo-loops.png` | 7 | 4 |
| zoo | Dashboards | `shots/rigs-zoo-dashboards.png` | 13 | 4 |
| chaos | Overview | `shots/rigs-chaos-overview.png` | 7 | 4 |
| chaos | Sources | `shots/rigs-chaos-sources.png` | 8 | 3 |
| chaos | Loops | `shots/rigs-chaos-loops.png` | 7 | 4 |
| chaos | Dashboards | `shots/rigs-chaos-dashboards.png` | 7 | 4 |
| longrun | Overview | `shots/rigs-longrun-overview.png` | 6 | 4 |
| longrun | Sources | `shots/rigs-longrun-sources.png` | 6 | 3 |
| longrun | Loops | `shots/rigs-longrun-loops.png` | 6 | 4 |
| longrun | Dashboards | `shots/rigs-longrun-dashboards.png` | 6 | 4 |
| sparse | Overview | `shots/rigs-sparse-overview.png` | 6 | 4 |
| sparse | Sources | `shots/rigs-sparse-sources.png` | 6 | 3 |
| sparse | Loops | `shots/rigs-sparse-loops.png` | 6 | 4 |
| sparse | Dashboards | `shots/rigs-sparse-dashboards.png` | 7 | 4 |
| bare | Overview | `shots/rigs-bare-overview.png` | 6 | 4 |
| bare | Sources | `shots/rigs-bare-sources.png` | 6 | 3 |
| bare | Loops | `shots/rigs-bare-loops.png` | 6 | 4 |
| bare | Dashboards | `shots/rigs-bare-dashboards.png` | 6 | 4 |

Daemon CPU (a few seconds after start, `sim ×60`/`×40` as configured):

| rig | daemon CPU % |
| --- | --- |
| plant | 20.0 % |
| torrent | 45.5 % |

The 6 baseline errors and 4 warnings appear on **every** rig, `bare.toml`
included, so they are not something a rig file can cause or fix: 3 are
`WebSocket connection ... failed: WebSocket is closed before the connection
is established` (`/ws/events`, `/ws/actuators`, `/ws/samples`, sometimes also
`/ws/readers`), and the rest are `404` on some resource the page requests
before the daemon has finished starting. The screenshots themselves render
correctly regardless (see e.g. `rigs-plant-overview.png`, `rigs-chaos-loops.png`)
— live data, gauges, warn/alarm colouring and loop detail all showed up as
expected in every rig tried. Extra errors on `dashboards` scale with channel
count (14 on `plant`, 13 on `zoo`) — one console message per generated tile,
worth a look by whoever owns the dashboard generator.

## Design notes worth recording

- **`Measurand` is interned by name for the whole process, not per file.**
  `range`, `precision`, `warn` and `alarm` are fixed by whichever reader
  declares that measurand name *first in the process*; every later
  declaration of the same name is silently ignored (only a unit mismatch
  raises). This is how `furnace.toml`'s three zones already share one
  band. It means `plant.toml`'s `warn = [25, 1300]` on `zone1` applies to
  *every* `temperature` channel in the file, ovens included — documented
  in-file, and it happens to be why the "≥2 warn, ≥1 alarm at start-up"
  requirement is trivially over-satisfied (18 channels start in warn). It
  also means two rig files loaded **in the same process** (as the test
  suite does) must not reuse a measurand name with a different unit, and
  should assume reused names share bands — I renamed `dual.toml`'s and
  `longrun.toml`'s readers away from `oven.toml`/`tank.toml`'s `thermocouple`/
  `level`, and `plant.toml`'s bench DMM away from `bench.toml`'s `dmm`, for
  exactly this reason (`Source`, separately, refuses a genuinely duplicate
  name outright).
- **`ambient` must equal `initial` for a gauge nothing drives**, or the
  reading decays to `ambient` over a few `tau_s` and then sits there. Caught
  this in `zoo.toml`'s voltage/current/power cells (initial 12 V/1 A/100 W,
  ambient wrongly 0) after a first screenshot showed them near zero;
  fixed by setting `ambient = initial` on cells with no actuator.
- `rig-up.sh`'s `rig-down.sh` did not kill the actual Vite process for any
  of the seven rigs brought up for this work — `nohup npx vite ...` puts a
  `npm exec` → `sh -c` → `node` chain between the recorded PID and the real
  process, so `kill` on the PID file leaves the `node .../vite` process
  running on its port. Killed them manually
  (`pkill -f "vite.*--port <port> --strictPort"`) before finishing; every
  daemon and Vite process from this session is now stopped. Worth fixing in
  the tool itself if other agents hit the same leak.
- `examples/simulated/rig.schema.json` (the generated editor schema) is
  stale against the current code: it does not know `feedforward` on a loop,
  and its `sim_furnace` schema rejects list-valued `power_w`/`capacity_j_per_k`
  — both of which `furnace.toml` already uses and which `flyball rig check`
  (the real, code-driven validator) accepts without complaint. Not fixed
  here — it is not in this agent's file list and regenerating it
  (`flyball rig schema > rig.schema.json`) touches a file every example
  rig's editor experience depends on.

## Wishlist: could not express in TOML/config

- **Resolved:** a program step to fail/restore a reader or disturb/set_limits
  an actuator. At the time this note was written, `programmer/loops.py` only
  had `regulate`, `ramp`, `hold`, `arrive`, `manual` and `programmer/activities.py`
  only `wait`, so `chaos-run.yaml`'s mid-run disturbance was driven from
  outside the program by `scripts/chaos-disturb.sh` instead. There is now a
  `command` step (`programmer/devices.py`'s `RunCommand`, tag `command`) that
  calls any device's own command — `{device_command: fail, actuator: zone3}`,
  `{device_command: disturb, actuator: heater5, args: {offset: -0.3}}` — by
  the same route the HTTP API and UI use; `chaos-run.yaml` uses it directly
  and no longer needs the script run alongside it. See
  [`programs.md`](../../book/src/3-running/programs.md#the-commands-every-rig-has).
- **Resolved:** `hold` can now time out. `Hold` (`programmer/loops.py`) takes
  its own `timeout` (seconds, not a `Duration` — `duration` is already the
  one field TOML/YAML may write flat) that ends the program if `duration`
  itself never elapses, the same as `wait`'s. `chaos-run.yaml`'s "a hold that
  times out" step is a `hold` with a `timeout` shorter than its `duration`,
  not a relabelled `wait`.
- **No unit named `bar` or `rpm`** in `flyball.core.units` — the brief's
  illustrative list included them, but only what `Unit.get` actually
  resolves went into `zoo.toml` (checked by importing every module and
  dumping the registry; see the base units enumerated in this agent's
  research, `Pa`/`kPa` and `1/min` stood in instead).
