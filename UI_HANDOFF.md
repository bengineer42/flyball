# flyball — UI and control handoff (16 Sep 2026, second pass)

Written for: whoever picks this up next — a person or an agent session — to know what is
built, what is verified, and what to do first. Everything here was verified in this session
unless marked otherwise. `HANDOFF.md` is the older *hardware* handoff for the humidity rig and
is unrelated. The previous version of this document (the first pass of 16 Sep) is superseded;
its §1–§2 content is now in `ui/DESIGN-SPEC.md` §10 (implementation log).

---

## 0. First five minutes

```bash
# the tree is NOT committed: ~145 files changed since fc59e2e, all verified. Commit first.
cd /home/ben/flyball && git status --short | wc -l

# backend: 297 tests, ruff clean; book builds strict
cd controller && uv run pytest -q && uv run ruff check src tests
uv run --group docs mkdocs build -f ../book/mkdocs.yml        # mkdocs, not mdbook

# UI: typecheck, build (447.9 kB gz), 14 store tests
cd ../ui && npm run typecheck && npm run build && npm test

# a rig + the dev UI, headless checks (see scripts/ui-check/README.md)
export FLYBALL_CHECK_DIR=/tmp/flyball-check
scripts/ui-check/rig-up.sh examples/simulated/furnace.yaml 8001 5201 --record
curl -X POST localhost:8001/api/programs/library/anneal/run
node scripts/ui-check/shot.mjs http://127.0.0.1:5201/#/ $FLYBALL_CHECK_DIR/shots/overview.png   # expect errors=0 warnings=0
node scripts/ui-check/dash-e2e.mjs http://127.0.0.1:5201 http://127.0.0.1:8001                  # expect ALL PASS
```

Open http://localhost:5201. Pages: Dashboards, Overview, Inputs (`#/inputs`, one signal at
`#/inputs/<address>`), Graph, Controllers (`#/controllers/<target address>`), Programs, Events,
Sessions, Simulation; a device page at `#/devices/<name>` (list at `#/devices`) is reached from
the others. Everything is named by **address** (`furnace.zone1`), a device by name, a
controller by the address of the signal it drives (`heaters.heater1`); see
`temp-docs/DEVICE-MODEL-PLAN.md` and `book/src/6-reference/api.md`.

---

## 1. State at handover (all verified)

| check | result |
| --- | --- |
| `uv run pytest -q` | 297 passed (was 259) |
| `ruff check src tests` | clean |
| `mkdocs build` strict | clean |
| `npm run typecheck` / `npm run build` | clean / 447.9 kB gz (was 405; +31 kB is `yaml`+`smol-toml`) |
| `npm test` (vitest, `packages/react/test`) | 14 passed |
| `dash-e2e.mjs` (22 editor checks) | ALL PASS |
| console on every page × 13 rigs | 0 errors, 0 warnings (was 6–16 errors per page) |
| perf, furnace `#/` 30 s (dev build) | TaskDuration 25.8 s → 2.4 s; `App` renders 2350 → 0 |
| perf, torrent (~11 600 samples/s) `#/` 60 s | 45.9 s → 11.6 s; long tasks 158 → 0; heap +5.8 % |

Rigs verified in the UI (every page, light/dark, 1440 and 400 px): `examples/simulated/{furnace,oven,tank,bench,chiller,dual}`,
`examples/stress/{plant,torrent,zoo,sparse,bare,chaos,longrun}`, `examples/humidity` (`uv run --group dev humidity-sim --port … --db … --speed 5`).

## 2. What was built this session (summary; detail in `ui/DESIGN-SPEC.md` §10)

- **Telemetry store** (`ui/packages/react/src/store/`): ring buffers per channel/loop, five shared sockets, per-widget subscriptions, charts fed by ref with `setData` in one rAF flush, off-screen and hidden-tab pause. `App.tsx` holds no live hook. Debug counters on `window.__fb`.
- **Dashboards engine finished**: 24 columns, view mode is plain CSS grid, react-grid-layout only in edit mode, server `problems[]` for unbound widgets, presets `examples/simulated/dashboards/{overview,furnace}.json`, switcher in the app bar. The "Maximum update depth exceeded" bug was `useStream` doing one `setState` per websocket message — it was never the dashboard code.
- **Visual language**: spec §1.1 tokens in `styles.css`, `theme.tsx` reads them, MUI `spacing: 4`, `PanelFrame` (status dot, severity borders solid/double/dashed, one-shot pulse), `SectionHead`/`StateBlock` on every list page, density toggle, focus rings, quiet app bar (alarm-summary chip, `● live`, REC dot, program, sim).
- **Loop faceplate**: PV / SP / OP rows with bars, SP notch, deviation, `AT LIMIT` + "requested N W", one-condition banner ("reader offline" wired from freshness), law/feedforward behind `<details>` on the L3 page, bare-uPlot mini trends (≈110 px plot, banded around SP). Same panel on the Actuators page shows the clamped output with the raw request as a caption.
- **Stale detection**: no sample for > max(3 × `period_s`, 5 s) in rig time → dashed border, hollow dot, "last sample N s ago". Stale is not an alarm.
- **Alarm summary**: `GET /api/health` `alarms {warn, alarm, max_level}` = channels outside bands + device conditions ≥ 30 (plant.toml reads 19 at start).
- **Humanised text**: `describeEventKind/describeSubject/describeStateKey/describeDevice/describeSimParam` in `packages/client/src/schema.ts`; Events, Sessions, Simulation, Loops, Actuators use them. `SourceDeclaration.label` on the wire; recorder persists source labels and actuator config (migration `0006_labels.sql`).
- **Control**: rate feedforward (`rate_gain` on `affine`/`table`; ramp supplies dSP/dt) — furnace zone 2 overshoot on a 15 °C/min ramp: plain PI 4.0 °C, static table 7.6 °C, table + rate 2.2 °C; zone 2 ships with it, zones 1/3 still on `none`. `Feedforward.invert` so `regulate(at=DEMAND)` is correct. `output_range` on actuator state/config (sim, SCPI, Modbus). Programs: `hold: {timeout}`, a `command` step (`device_command:` — `command` is the step tag on the wire), and a real **failed** state (ERROR events `step_failed` + `failed`; `/api/programs/running` carries `failed`/`error`; the UI shows it in the app bar, Programs page and widget).
- **Program builder** parsers replaced by `yaml` + `smol-toml` (`programText.ts` 1049 → 129 lines).
- **Stress rigs** (`examples/stress/`, all TOML, each with a program): `plant` (12-zone furnace + ovens + tanks + SCPI bench + Modbus; 41 channels, 20 units, 17 loops), `torrent`, `zoo` (one channel per unit), `sparse`, `bare` (a rig file can be just a name), `chaos` (fails a reader and disturbs a heater from its program), `longrun` (600×). Ordinary `chiller` (reverse-acting) and `dual` in `examples/simulated`. `tests/test_stress.py` + `test_examples.py` cover them.
- **Docs**: `book/src/3-running/{dashboards,ui,programs}.md`, api/rig-file/loop references, `UI.md` §4 points at the spec, `ui/README.md` Theming + Performance, `examples/*/README.md`.
- **Tooling**: `scripts/ui-check/` (rig-up/down, shot with console capture, measure, perf, dash-e2e, contrast, sweep).

## 3. Next steps, in order

1. **Commit.** Suggested split: backend (feedforward/output_range/recorder/programmer/health), stress rigs + tests, UI store + dashboards, UI visual pass (tokens/PanelFrame/faceplate/humanise), docs + tooling.
2. **Design question (needs the user):** `range`/`precision`/`warn`/`alarm` live on the interned `Measurand` (`core/reading.py`), so two channels sharing a measurand name share bands, and a °C `temperature` cannot coexist with a K `temperature` in one process. They belong on `Channel`. Only 7 places in `src` read them off the measurand (`runtime/simulation.py`, `server/routes/devices.py`, `server/schemas.py`), but it changes the rig-file semantics. `zoo.toml` uses `zoo_*` measurand names as the workaround.
3. **Websocket batching**: the daemon sends one message per sample; on `torrent` ~25 % of a core is browser message dispatch before any JS runs. Batch samples per frame server-side (one message carrying many samples); the store already ingests arrays.
4. **Overview with many same-unit sources** is a wall of identical tiles (`plant`): default to the channel table (spec §3.14) above ~12 sources of one unit, tiles one click away.
5. **Spec gaps still open** (`ui/DESIGN-SPEC.md` §10 "outstanding"): "By thing" add-widget picker with live previews (§4.3), keyboard move/resize of widgets (§4.5), server-side rig default + generated `/api/dashboards/overview` (§4.7), import auto-save (§4.9), `readOnly`/`title`/`defaults` on the document.
6. **Program libraries** are shared per directory (every stress rig imports all five stress programs; every simulated rig imports the furnace ones, and now correctly *fails* when a loop is missing). A `programs =` key per rig file, or `programs/<rig>/`, would fix the noise.
7. **Small**: extend `rate_gain` to furnace zones 1/3 (needs 3000 and 2200 J/K); `loop.mode "open"` is never emitted (UI keys on `law.tag === "open_loop"`); `LoopOut` has no ramp target/rate so the SP caption says "following linear ramp setpoint"; light-mode `--fb-ok/--fb-warn/--fb-stale` are 3.96–4.24:1 against `bg-0`/`bg-2` (fine on `bg-1`); default chart window on a channel with hours of history (`longrun`) is still 5 min; `DeviceCard`/`ReaderCard` use a `StatusDot` but are MUI cards, not `PanelFrame`; lazy-load the program builder route to claw back the +31 kB.

## 4. Known issues / gotchas

- The simulated clock runs at 60× (600× on `longrun`), so "stale" and relative event times are in **rig** time — the store's `nowS()` is the newest sample across the rig; do not compare against wall time.
- Measurand interning (§3.2) also bites tests: `Measurand.forget("temperature")` before building a rig whose precision differs from an earlier test's (`test_simulation.py` does this now).
- `examples/simulated/rig.schema.json` is gitignored and goes stale: `uv run flyball rig schema > ../examples/simulated/rig.schema.json` after changing any config model.
- `SimActuator` in "smart" mode (demand in the output's unit, `oven`/`tank`) has no `output_range` (None) by design, so the Actuators page cannot show at-limit for it.
- `hold.timeout` is a plain seconds float, not a `Duration`, because `server/dialect.py`'s `foldable()` allows one `Duration`-typed field per command and `duration` owns it.
- Vite is configured with `usePolling`; if pages come up blank after a lot of file churn, restart it. Killing the daemon with SIGKILL leaves the session open (self-healing on the next start). `rig-up.sh` uses `setsid` so `rig-down.sh` can kill the whole group — the first version leaked Vite processes.
- Ports 5173/5180/5182 have Vite dev servers from before this session (packages/react dev harnesses); harmless.

## 5. Working agreements (from the user)

- **Never** `git stash` / `git checkout` / `git reset` to look at old state. No commits without being asked.
- Headless Playwright only (`scripts/ui-check/shot.mjs`), never the headed MCP browser. Look at the screenshot before claiming something looks right; measure (`measure.mjs`) rather than eyeball sizes.
- MUI only in `apps/dashboard`; `packages/react` is pure (CSS variables `--fb-*`), panels are data-in/events-out; `packages/client` is the typed API. The UI is a library first.
- Units next to every value; hover hints instead of brackets in labels; no text that looks like a variable name; "Stop" not "delete" for loops.
- Simulation-only commands (`fail`, `restore`, `set_limits`, `disturb`) live on the Simulation tab, never on the Actuators tab (verified on every rig).
- Show evidence (test output, screenshots, console counts, measured numbers) for anything claimed done. British English, no filler.
- Parallel agents are fine; cheaper models for mechanical work, the expensive one only for architecture; tell each agent which files the others own, re-read before editing, small targeted edits. Take *before* screenshots before editing, every time.

## 6. Where things are

| | |
| --- | --- |
| Framework | `controller/src/flyball` — core, control, runtime, programmer, server, db, sim |
| Examples | `examples/simulated/*.yaml` (+ `programs/`, `dashboards/` at `schema_version: 2`), `examples/stress/*.yaml` (+ `programs/`, `scripts/`), `examples/humidity` (`rig.yaml` + `sim.yaml`, own pyproject) |
| UI | `ui/packages/client` (wire types by address), `ui/packages/react` (`store/` keyed by address, `panels/`: `DeviceSignals`, `ControllerPanel`, `WritePanel`, `DevicePanel`, `WaitPrompt`, `hooks/`), `ui/apps/dashboard` (`dashboard/`, `widgets/`, `pages/`: `Overview`, `Inputs`, `Graph`, `Controllers`, `Devices`, `Programs`, `Events`, `Sessions`, `Simulation`, `Dashboards`) |
| Spec and research | `ui/DESIGN-SPEC.md` (§10 = implementation log), `ui/UX-RESEARCH.md` (§6 = what the stress rigs showed), `UI.md` (original plan), `DECISIONS.md` |
| Book | `book/src` (mkdocs; `uv run --group docs mkdocs build -f ../book/mkdocs.yml` from `controller/`) |
| Checks | `scripts/ui-check/` (README there) |
