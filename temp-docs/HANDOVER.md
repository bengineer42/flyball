# Device model — what was done, where it stands (16 Sep 2026, end of session)

Written for the next session to re-plan from. Everything below is on branch `device-model`
(23 commits over `main`, last `c1662fe`), verified at that commit unless marked. The two
planning documents this work was built from were deleted at the end of the session by
request; a copy sits in the previous session's scratchpad only.

## 1. The model, as built

- **Device** = a named thing with a **tree of signals** (namespaces group them), commands,
  state/conditions, a poll period. Trees are static per device instance (`bind` once; a
  device whose signals change is replaced, not rebound). `Device.atomic = True` makes the
  root read as one sample. Drivers subclass `Device` + `DriverConfig[D]` (`build(name, label)`);
  the file key is `driver:`; a driver config may not use an envelope key (`driver label
  poll_s signals bound config`), so generic drivers declare their tree as `ports:` (sim),
  `channels:` (scpi/pymeasure/qcodes), `registers:` (modbus/i2c_table), `sensors:` (sht4x_set).
- **Signal** = one quantity (name + unit; not interned) on one device, with an **address**
  (`device[.ns…].signal`, `Path` objects, strings only at the wire/rig file) and **access**
  R (read on demand) / P (published: streams + recorder) / W (writable); `P ⇒ R`. The driver
  declares access; the rig file's `signals:` overrides metadata (label, range, precision,
  warn/alarm, poll_s, limits) and may only remove access. `dtype`/`shape` exist on the spec
  (`float`/`()` only for now, refused otherwise) so other types can come later.
- **Sample** = readings of signals under one node at **one instant**, keyed by the bound
  `Signal` objects (`by_name()` for the wire), possible fields fixed at bind, any may be
  missing, never empty; may carry any readable signal but only P signals reach streams and
  the recorder. **Demand** = the write-side twin (W signals under one node, one atomic write).
  **Reading**, **WriteState** (value, requested, at_limit, controller).
- Writes are two-phase: `Device.apply(signal, t, value)` records; `observe(reading|sample)`
  for bound inputs (`bound: {role: address}` in the file; node subscriptions deliver a
  P-filtered sample); `commit(t)` pushes once per delivery for every touched device.
  `blocking = True` runs `commit` on a writer thread.
- **`together`** on a `SignalSpec`: siblings that must be in one demand. Only the humidity
  blender uses it. Under review (see §5).
- **Controller** binds one P source to one W target through a law (+ feedforward); named by
  the target address; `Controller` absorbed the old `Loop`. Set-point **generators** register
  by tag, serialise from their `__init__`, are accepted on `regulate`/`reference` (`at` = a
  number, `process`/`setpoint`/`demand`, or `{tag, …}`; `start` = where a generator begins);
  `LinearRampSetpoint`, `Hold`, `Profile` (segments back to back); `finished(time)` in rig time;
  `arrived` on the wire.
- **Rig file**: `name`, `board`, `recording`, `clock`, `links`, `devices: {name: entry}`,
  `controllers: {target: {signal, law, feedforward, default}}`; YAML documented, TOML accepted;
  strict loader (duplicate keys refused); overlays (`flyball-daemon a.yaml b.yaml`: mappings
  deep-merge, scalars/lists replace, `null` deletes), `extends:`, `--set k=v`; `rig check
  --print` prints the canonical layered form. The old `readers/actuators/loops` sections are
  refused. Board profiles (`pin:`) still resolve into device entries.
- **Sim**: `sim_plant` (`model:`) / `sim_furnace` links; `sim_daq` (`ports:`, dotted keys →
  namespaces, `fail`/`restore`) and `sim_drive` (`ports:`, long form `{port, quantity, unit,
  limits}`; `demand: output` = the plant inverted so a controller hands it the setpoint;
  `disturb`) over any `MultiPlant`. Every example rig is YAML in `examples/simulated`,
  `examples/stress`, `linux/examples`, `examples/humidity`.
- **Store**: migration `0007_devices.sql` (device, signal, write, write_state, controller;
  legacy rows carried where they map); `Recorder(writer, signals, controllers)`; history and
  export by address. **Wire** (`book/src/6-reference/api.md` is current): `/api/devices`,
  `/api/read/{address}` (+`?fresh`, `?at=a,b`), `PUT /api/signals/{address}`, `PUT
  /api/devices/{name}/demand`, `POST /api/devices/{name}/commands/{tag}`, `/api/controllers…`,
  `/api/waits` (the trigger registry; `Signal`→`Trigger` rename), streams `/ws/{samples,
  writes, controllers, devices, waits, events}` (`samples` is a newest-per-node flush at
  ≤20 Hz, not every sample). Program check reports `warnings` per step for what the rig
  lacks; the daemon loads `programs/` and `tunings/` beside the first rig file.
- **Packages**: `controller` (533 tests, pyright 0), `linux` (`flyball_linux`: i2c/spi/gpio/
  pwm/onewire links; sht4x, sht4x_set, ds18b20, ads1115, mcp3008, i2c_table, gpio_line,
  pwm_channel; 74 tests), `examples/humidity` (depends on flyball-linux; `dual_pump_blender`,
  `sim_humidity_chamber` — a physical mixing model that is also the blender's PWM link so the
  real driver runs in the sim; `rig.yaml` + `sim.yaml` at full address parity; `programs/
  demo.yaml`, `tunings/`; 26 tests). Both books rewritten, `mkdocs --strict` clean.
- **UI** (`ui/`): client and store keyed by address; panels `ControllerPanel`, `WritePanel`,
  `DeviceSignals`, `DevicePanel`, `WaitPrompt`; app pages Dashboards, Overview (tiles boxed by
  sample), **Devices** (readings by namespace; no Inputs tab), Graph, Controllers (faceplate
  with ramp entry; a value-or-generator redesign was in flight and killed), Programs (builder
  keeps what the rig lacks, marks it with the check's warnings; device/command picker; `set`
  step), Events, Sessions, Simulation, device pages `#/devices/<name>`; dashboard documents
  v2 (addresses, controller names). Titles from labels, units without brackets, dimensionless
  shows nothing. Sweep: 462/462 headless shots clean over 14 rigs, dash-e2e ALL PASS.

## 2. Commits (oldest first)

`369efaa` names freed · `aa90c30` core (step 1) + overlays · `78492ed` rig runtime + devices
in the file (step 2) · `7ef0cc1` signal-keyed samples · `993554c` static trees, namespaces in
driver config, P-only subscribers · `e808217` drivers/recorder/server, legacy deleted (3–5) ·
`e5b4e4b` linux pkg, smart sim, program check, atomic roots · `35addc8` book · `6e23e5c`
humidity on flyball-linux + demo · `2ee1017` humidity CLI routes · `e4cd14b` detach fix ·
`1fc248a` UI (step 6) · `5bccc14` generators on the wire · `08cb8fb` sweep tooling ·
`8d80cf2` arrival/Hold/Profile · `ecfe040` Devices tab, `signal` args as enums · `8542fd6`
physical chamber sim · `359271e` · `46c96b0` `set_blend` (BlendFlow union restored) ·
`65d4052` builder fidelity · `00367ac` faceplate ramp + text widget · `930fe15`/`b7cced6`
generator `start` · `f87efe3` write rows (groups, sliders, settings) · `c1662fe` titles/units/
sample boxes.

## 3. Uncommitted in the working tree (decide: keep or discard)

- `examples/humidity/src/humidity/blender.py` — Ben's edit: `set_blend(flow, humidity)`,
  `set_flows(dry, wet)`, `set_efforts(dry, wet)` commands. Assessment: the `humidity`
  argument bypasses the controller's ownership of `blender.humidity`; the two new commands
  duplicate the flow/effort demands; none of them survive the next supply reading (see §5).
- Killed agents' partial UI edits: `ui/apps/dashboard/src/pages/Controllers.tsx`,
  `ProgramBuilder.tsx`, `Programs.tsx`, `programDoc.ts`, `ui/packages/client/src/{rig,wire}.ts`,
  `ui/packages/react/src/styles.css` — a value-or-generator target control with a
  schema-driven form and start choice (U4), and a `set` step that offers write groups (U7).
  Unknown state; typecheck before trusting; `git checkout --` these to discard (Ben's rule:
  never stash).

## 4. Known bugs and gaps

- **Blender mode** (real): `commit` re-applies the blend whenever nothing is pending, and a
  supply reading touches the blender every second, so a manual flow/effort (demand or
  command) is overridden a second later. The driver has no explicit mode.
- Torrent perf numbers were taken under unrelated machine load (ffmpeg ~80 %): 14–17 s vs
  the 11.6 s baseline is unexplained; re-run on a quiet machine
  (`scripts/ui-check/perf.mjs`, `examples/stress/README.md`).
- Events page on `torrent` is swamped by "running slow" device warnings (default view).
- Shared `examples/stress/programs/` library: `plant` imports `chaos-run` and faults its own
  `furnace.zone3`; `bare` shows a failed `zoo-tour`. A `programs:` key per rig would fix it.
- `Gauge` on `#/inputs/blender.dry_flow` uses 0–100 for a 0–2 signal.
- A generator registered outside `control/setpoint.py` is accepted at the top level but not
  as a profile segment (the recursive union is built at import).
- The UI doesn't render profiles or `arrived` yet; `reference` display exists for ramps.
- `Ref` (links.tsx) has no `title` prop, so a tile caption's hover shows the device, not the
  namespace address.

## 5. The open design question (why re-planning)

**How manual operation of a composite actuator is expressed.** Today the blender exposes
manual operation as writable signals tied by `together` (`dry_flow`+`wet_flow`,
`dry_effort`+`wet_effort`) plus an RW setting (`blend_flow`), and `humidity [W]` for the
controller. Consequences: the rig's demand check, the Devices tab, the `set` step and the
driver's `commit` each rediscover the groups; the UI shows six sliders; and there is no mode.
Ben finds this "overly complicated" and "much less nice" and keeps reaching for commands.

The alternative on the table (Ben's direction; not built):

```
signals   humidity [W]                       the controller's target
          flows.dry/.wet [RP]                readbacks, one sample (atomic namespace)
          efforts.dry/.wet [RP]              readbacks, one sample
          expected_humidity [RP]
commands  set_blend(flow: BlendFlow)          → mode BLEND   (Absolute | OfBlendMax | OfGuaranteedMax)
          set_flows(dry, wet)                 → mode FLOWS
          set_efforts(dry, wet)               → mode EFFORTS
          stop()                              → FLOWS, both 0
mode      a humidity demand puts it in BLEND; a supply reading re-blends only in BLEND
```

Rule that falls out: **W signals are what controllers drive; commands are what operators
do**; readbacks are RP. `together` would then be unused (candidate for removal from the
core), `blend_flow` is the settings tier not a signal, and the UI needs no write-group
machinery. Trade-off: a command's effect isn't recorded as a write state (readbacks are).
Wider questions to settle in the re-plan: whether *any* device should have operator-writable
signals beyond controller targets (e.g. a PSU `set_voltage [W]` with no controller); whether
atomic namespaces should replace `together` for the cases that remain; what the Devices tab
and the `set` step show under that rule.

## 6. How things run

```bash
cd examples/humidity && uv run flyball-daemon rig.yaml sim.yaml --port 8000 --record   # the sim
cd ui && FLYBALL_URL=http://127.0.0.1:8000 npx vite --config apps/dashboard/vite.config.ts apps/dashboard --port 5173
cd controller && make lint imports test && uvx pyright --pythonpath .venv/bin/python src/flyball
cd ui && npm run typecheck && npm run build && npm test
cd controller && uv run --group docs mkdocs build -f ../book/mkdocs.yml --strict
scripts/ui-check/{rig-up,sweep,shot,dash-e2e,perf}.sh|.mjs   # headless checks (README there)
```

Working agreements that held all session: headless Playwright only; evidence per claim;
cheaper agents for mechanical work; no `git stash`/`checkout`/`reset`; commit per step when
green; British English; no `[unit]` brackets, hover hints not text.
