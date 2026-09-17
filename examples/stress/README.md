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
driving them from demands, `scpi`/`modbus` over the fakes) and
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
[`programs.md`](../../book/src/1-running/programs.md#the-commands-every-rig-has)
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

Re-measured on this machine on 16 Sep 2026 (device-model wave, third pass),
same method as before — `--record`, `reading` row count in the SQLite store
ten seconds apart:

```
count at t:      1,973,460
count at t+10.02s: 2,076,256
rate: (2076256 - 1973460) / 10.02 ≈ 10,255 samples/s
```

Still comfortably over the 1000/s target, and close to the original pass's
~11,568 samples/s (the file and clock speed are unchanged; the small
difference is machine load — see the CPU note below — not a regression).

## Measured on this machine, 16 Sep 2026 (device-model wave, third pass)

The page set, addresses and API changed under the device model (`Sources` →
`Inputs`, `Loops` → `Controllers`, plus a `Devices` page); the table below
replaces the first pass's, which named pages that no longer exist. Full
sweep: all 13 rigs (the 7 here, plus `bare`/`sparse`/the 6 ordinary rigs in
`../simulated/`) × all 10 pages (`dashboards overview inputs graph
controllers devices programs events sessions simulation`) × 3
configurations (1440 light, 1440 dark, 400 px) via `scripts/ui-check/
sweep.sh`, using its own `shot.mjs`-based error/warning/pageerror count —
**every single shot came back `errors=0 warnings=0 pageerrors=0`**: 429/429
across the whole fleet. (Screenshots: `sweep-<rig>-<page>[-dark|-400].png`
under the check dir's `shots/`; the old `rigs-*.png` naming from the first
pass is gone.)

| rig | shots | clean |
| --- | --- | --- |
| plant | 33 | 33 |
| torrent | 33 | 33 |
| zoo | 33 | 33 |
| sparse | 31 | 31 |
| bare | 30 | 30 |
| chaos | 33 | 33 |
| longrun | 33 | 33 |

(`sparse`/`bare` have fewer shots because there's no controller — and for
`bare`, no device or signal either — to open a detail page for.)

The first pass's 6 baseline console errors and 4 warnings (constant
WebSocket-closed-before-connect and startup-race 404 noise) are **gone**:
nothing in the current UI produces them on any rig. Live data, gauges,
warn/alarm colouring, loop faceplates and the dashboard generator all
rendered correctly in every rig checked (screenshots inspected, not just
counted, for `furnace`, `plant`, `bare`).

One thing the first pass didn't see: with the shared per-directory program
library (the `programs:` key in a rig file points elsewhere), a rig that doesn't have the
loop a shared program regulates now visibly **fails loudly** instead of
silently — e.g. `bare`'s Overview shows a red "program failed" banner
naming a `zoo-tour` step, because the sweep tries every stress program in
turn and `bare` has no devices at all. This is a real 200-response async
failure (`run_from_library` → `started` → `step_failed` → `failed`, all in
the event log), not a UI bug — confirmed against the store directly.
Likewise `plant`'s Overview can show `furnace offline` if `chaos-run` (which
targets `furnace`/`heaters`, names `plant.yaml` also uses) is the program
that happens to run there.

Daemon CPU (`top -b -n1 -p <pid>` on the `flyball-daemon` process, not the
`uv run` wrapper, a few seconds after start):

| rig | daemon CPU % (first pass) | daemon CPU % (this pass) |
| --- | --- | --- |
| plant | 20.0 % | 10–27 % (noisy; a couple of readings) |
| torrent | 45.5 % | ~90 % |

**Torrent's CPU and the perf numbers below are not directly comparable to
the first pass**: this machine had substantial unrelated load running
during this measurement (two `ffmpeg` transcodes at 82 %+6.6 % CPU, several
browser renderer processes, load average 4.0 vs whatever baseline the first
pass had) — confirmed with `uptime`/`ps` at measurement time. `plant`'s
number is close enough to the original to be within noise; `torrent`'s ~2×
increase is plausibly this contention rather than a code-level regression,
since the relevant server-side mitigation (`server/routes/telemetry.py`,
`FLUSH_S = 0.05` — 20 Hz max, one coalesced frame per socket per flush,
newest-value-per-node) is unchanged from what the first pass already
measured against.

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
  **Fixed as of the device-model wave**: `rig-up.sh`/`rig-down.sh` now use
  `setsid` and kill the whole process group (`kill -- -"$pid"`), confirmed
  clean across this pass's 13-rig sweep (ports checked with `ss` after every
  teardown) and one-off runs of `furnace`/`plant`/`torrent`/`chaos`/`zoo`.
  One humidity teardown in this pass showed the daemon/vite still listed by
  `ps` immediately after `rig-down.sh` printed "stopped", but gone a few
  seconds later on a recheck — plausibly signal-delivery delay rather than a
  live leak, since it self-resolved and every other teardown this pass was
  immediate; flagged, not filed as a confirmed defect.
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
  [`programs.md`](../../book/src/1-running/programs.md#the-commands-every-rig-has).
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
