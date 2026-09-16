# Device model 2 — one store, descriptors, demands and commands

Design of 16 Sep 2026, then built the same day on branch `device-model-2`
(Python: controller, linux, humidity; the UI is next). Sections marked
**as built** say where the code settled; the code is the truth where they
differ.

## 1. Words

| word | meaning |
|---|---|
| **structure** | what a device *is*: its tree, roles, quantities, dtypes, sections. Declared once; per class when hand-written, per instance when generated from config. |
| **variables** | what *this* instance has: limits, ranges, bands, labels, poll. Merged from class defaults, config, inputs and the rig file. |
| **effective** | a variable's value once merged and computed at build (`max_flow` from pump calibration). |
| **config** | a value from the rig file, effective at build, shown but never set at run time. |
| **input** | a value the device is *given*: another device's signal the rig binds to a role. |
| **demand** | anything *settable*, with a current value (its readback) that updates. Set by a command, or moved indirectly by another (`set_efforts` moves the flows). What a controller drives. |
| **output** | a value the device *produces*; never set. Includes what used to be state: `mode`, `blend`, `conditions` are outputs with a non-float dtype. |
| **command** | a method a person or a program runs. Records; `commit` does the I/O. |
| **mode** | which command is in force; what `commit` reads. An enum output. |
| **section** | a second grouping axis across the tree (`dry` / `wet` / `total`), a tag on the signal; the address comes from the path only. |
| **readback** | a demand's current value (`.VAL` vs `.RBV`). |

Rules: controllers drive demands; people and programs run commands; outputs
are read. `together`, `settings`, `state`, `WriteState`, `observe` and
`blend_flow`-as-signal no longer exist.

## 2. One store

Every value a device has lives in one place: the rig's `latest`, newest
`Reading` per signal. A device holds no copies. It *reads* the store when it
needs a value (in `commit`, in `read`) and *pushes* to the store when it has
one. The rig keeps only the dependency (which devices to commit when a
signal lands — declared by the input descriptors) and the touched set.

```
push(signal, reading)  ──► latest[signal] ──► streams (newest per key, ≤20 Hz)
                                          ──► recorder (P signals only)
                                          ──► controllers (source signals)
                                          ──► touched devices (input signals)
```

- **Typed readings.** `dtype` is `float | int | bool | str | enum | json`
  (a pydantic type on the descriptor); `shape` as now. The recorder stores
  non-float values in a typed column; history and export by address work
  for them, so "when did mode change" is a query.
- **A demand's reading** carries `requested` and `at_limit` (from the clamp)
  and `controller` (from the rig). That is the old `WriteState`, folded in.
- **A `Sample`** is the group of pushes made at one instant on one node —
  the unit of push, kept so a graph and a derived value see numbers that
  coexisted. Not a tier.
- **`P` vs `R` stays.** Pushing to the store is not the same as recording and
  streaming; an `R`-only output is readable on demand and nothing else.
- **Devices don't import the rig.** A `Readings` protocol (`get(signal)`,
  `push(signal, reading)`) is injected at bind; the rig implements it, a
  test implements it with a dict, and a blocking device's writer thread gets
  a frozen snapshot of the inputs taken under the lock at hand-off.
- **Derived values** are signals only if something other than a screen
  consumes them (recorder, program wait, controller, another device's
  input). A UI-only total is the UI's, derived within one sample.

## 3. Structure: descriptors

The primitive is a factory that makes a spec; generated drivers call it in
`__init__` from config, hand-written drivers use it in the class body and
`__set_name__` collects the result. Both produce the same bound objects and
the same schema.

```python
class DualPumpBlender(Device):
    dry, wet, total = Section("dry", "Dry line", axis="line"), Section("wet", "Wet line", axis="line"), Section("total", "Total", axis="line")

    flows     = Node("flows", "Flows")
    efforts   = Node("efforts", "Efforts")
    max_flows = Node("max_flows", "Max flows")
    supply    = Node("supply", "Supply humidities")

    dry_max_flow = max_flows.config(dry, "Dry max flow", Flow)
    wet_max_flow = max_flows.config(wet, "Wet max flow", Flow)

    dry_supply = supply.input(dry, "Dry line humidity", Humidity, default=dry_supply_config)
    wet_supply = supply.input(wet, "Wet line humidity", Humidity, default=wet_supply_config)

    humidity   = Demand("humidity", "Target humidity", Humidity, limits=(dry_supply, wet_supply))
    dry_flow   = flows.demand(dry, "Dry pump flow", Flow, limits=dry_max_flow)
    wet_flow   = flows.demand(wet, "Wet pump flow", Flow, limits=wet_max_flow)
    dry_effort = efforts.demand(dry, "Dry pump effort", Effort)
    wet_effort = efforts.demand(wet, "Wet pump effort", Effort)

    expected_humidity = Output("expected_humidity", "Expected humidity", Humidity)
    mode  = Output("mode", "Mode", Mode, initial=Mode.BLEND)
    blend = Output("blend", "Blend flow", BlendFlow, initial=DefaultBlendFlow)

# a generated driver, same objects
    for name, port in config.ports.items():
        self.tree.add(Node(name, port.label).output("value", "Value", port.quantity))
```

- On the class the name is the spec; on the instance it is the bound thing:
  `self.dry_flow.value` (from the store), `self.dry_supply.value`,
  `self.humidity.pending`, `self.mode` (an output of enum dtype; assignment pushes).
- A `Section` supplies the segment name by default and tags the signal
  (`tags: {line: dry}`); segment and tag are separable in the factory
  (`flows.demand("ch1", …, section=bank_a)`). A node declared from a
  `DryWet` type gets sections from its fields. Sections are never
  addressable — one address per signal.
- `name` is the address segment; `label` the display text, overridable in
  the rig file. Unchanged.

### Variables and their references

| reference | resolved | example |
|---|---|---|
| number | at class | `(0.0, 1.0)` |
| a `config` descriptor | at build, from `device.config` | `limits=dry_max_flow` |
| an `input` descriptor | live, from the store at demand time | `limits=(dry_supply, wet_supply)` |
| rig file `signals:` | at bind, numbers and labels only | `zone1: {label: …, warn: [..]}` |

Merge order: class defaults → config → rig file. Live references are resolved
by the rig when it clamps and by the schema route for display; only inputs
and outputs may be sources, so no cycles. A missing input uses the
descriptor's `default` (a config reference) and `rig check` warns.

## 4. Read and commit are derived

The base `Device` has neither `read` nor `commit`. From the descriptors:

- any `demand`, or any command with `commit=True` ⇒ the class must implement `commit(time_ns)`;
- any `output` ⇒ it must have a way to produce: `read(time_ns)`, or pushes from `commit`.

Checked when the structure is complete (class creation, or the end of
`__init__` for a generated tree); stored as `readable` / `writable` and on
the wire. `Readable` / `Committable` mixins carry the method signatures.
A device with inputs and demands and no poll (the blender) is normal.

## 5. Commands

**As built.** Commands do their own I/O at once (`set_flows` writes the
pumps); `commit=True` is opt-in for one that only records. `@command(tag,
simulation, commit, mode, interrupts)`. A linked argument is
`Annotated[<type>, <descriptor>]` (`For[...]` was dropped: a checker
cannot type it) or a parameter named like a descriptor. A command with a
`mode` or a linked argument is refused while a controller is active
unless `interrupts=True`, in which case the controller is put in manual
first with an `interrupted` event; a command with neither (maintenance)
runs regardless. `owner_exempt` does not exist. `WriteState` and
`device.written` remain as the write record for the wire and the store;
the readback reading is what the driver pushed, else the committed value.
Every scalar demand no command links to gets `set_<path>`; on the wire a
linked argument is optional (the rig fills it from the readback).

```python
    @command(commit=True, mode=Mode.FLOWS)
    def set_flows(self, dry: For[dry_flow], wet: For[wet_flow]) -> None:
        self._request = SupplyFlows(dry, wet)

    @command(commit=True, mode=Mode.STOPPED, owner_exempt=True)
    def stop(self) -> None: ...

    @command                       # maintenance: at once, no commit, no ownership check
    def restore(self) -> None: ...
```

**As built.** Two spellings link an argument to a demand, and by-name is
the preferred one: a parameter named exactly like a descriptor
(`def set_flows(self, dry_flow: Flow, wet_flow: Flow)`) links to it with
no annotation. `Annotated[<type>, <descriptor>]` is for a parameter that
must be called something else; resolved with
`get_type_hints(fn, include_extras=True, localns=vars(cls))`. `For[...]`
was dropped: a checker cannot type a subscript on a value.

Every scalar demand with no command linking to it gets a synthesised
`set_<name>(value)`, so `PUT /api/signals/{address}`, a program `set` step
and a controller's write are all that one command: one path for ownership,
clamp and record.

A `CommandSpec(tag, fn, params, commit, mode, owner_exempt, simulation)` is
built once; each `Param` knows its descriptor, so:

**Schema** per parameter: title from the label, unit, `minimum`/`maximum`
from this instance's effective limits, `x-signal` the readback's address;
`x-mode` on the command.

**Run time**, in the rig, before the method:
1. a parameter not supplied takes the linked demand's current value;
2. clamp to effective limits; keep `requested` where it changed;
3. ownership: refused if the device's demands are driven by a controller, unless `owner_exempt`;
4. run under `rig.lock` (or on the writer thread if `blocking`);
5. `commit=True`: set `mode`, mark touched, commit — at once when run alone,
   once per device at the end of a delivery or a program `group`;
6. push the demand's reading with `requested`; push `mode`; push
   `last.<tag>` (a json output the base class declares) with `{args, at}`.

**Program groups.** A `group:` step runs several commands and `set`s on one
or more devices, then one commit per device touched. The delivery model
applied to a step.

## 6. The blender

```python
    def commit(self, time_ns: int) -> None:
        if self.humidity.pending is not None:
            self.mode = Mode.BLEND
        match self.mode:
            case Mode.BLEND:
                supply = SupplyHumidities(self.dry_supply.value, self.wet_supply.value)
                fraction = calculate_wet_fraction(supply, self.humidity.value)
                self._pumps.set_blend(self.blend, float(fraction))
                self.humidity.at_limit = rail_of(fraction)
            case Mode.FLOWS:   self._pumps.set_flows(self._request)
            case Mode.EFFORTS: self._pumps.set_efforts(self._request)
            case Mode.STOPPED: self._pumps.stop()
        out = self._pumps.output
        self.dry_flow.push(out.flows.dry);   self.wet_flow.push(out.flows.wet)
        self.dry_effort.push(out.efforts.dry); self.wet_effort.push(out.efforts.wet)
        self.expected_humidity.push(expected_humidity_from_flows(out.flows, supply))
```

No `read`, no `observe`. A supply reading re-blends only in BLEND. A manual
flow survives until a humidity demand or another command changes the mode.
Optional: `commit(time_ns, changed: set[Signal])` so a device on a slow bus
can skip work; the blender doesn't need it.

## 7. Wire

**As built.** `/ws/samples` carries typed values; `/ws/writes`,
`/ws/devices` and `/ws/controllers` still exist (fold `writes` and
`devices` with the UI). `DeviceOut`: `inputs`, `readable`, `writable`,
`conditions`, no `state`. `SignalOut`: `role`, `tags`, `initial`,
effective `limits`, no `together`. `CommandOut`: `commit`, `mode`,
`interrupts`, `demand_of`, `links`. The schema route: `x-signal`, `unit`,
effective `minimum`/`maximum` per linked argument, `required: []` for
them. `POST .../commands/{tag}` returns the method's return value.

One stream of signal updates, newest per key, ≤20 Hz, grouped by sample;
`/ws/writes` and `/ws/devices` fold into it (`offline`/`slow` are pushed by
the runtime onto the base class's `conditions` output). `/ws/controllers`
stays. `GET /api/devices/{name}/schema` is answerable from the structure plus
the instance's effective variables; `SignalOut` gains `dtype`, `role`,
`tags`. Measured: a full `DeviceOut` ≈ 80 µs / 3.3 KB to encode, a sample
≈ 5 µs / 230 B; non-float pushes happen on action, so cost is nil.

## 8. Fit

| thing | fit |
|---|---|
| qcodes / pymeasure | settable parameter → demand with readback; gettable → output; methods → maintenance commands |
| modbus / i2c tables | holding register → demand, input register → output; `commit` writes the block once |
| sims | outputs and demands from `ports:`; `fail`/`disturb` are `simulation=True` commands |
| controllers | drive demands; `Reading.controller` says who |
| recorder / history | typed rows; enums queryable |
| programs | `set` = synthesised command, `command`, `group`, `wait` on any dtype |
| UI (later) | render by dtype; pivot by section; one card per write command; mode highlighted |

## 9. Order of work (Python first)

**As built:** steps 1–6 are done (commits b9f0b47 … on `device-model-2`);
step 7 (the book) is in progress. Also: `Role.SETTING`; `conditions` is an
`RP` json output on every device, the runtime's `offline`/`slow` stay on
`DeviceRun`; `last.<tag>` is an `RP` json output per command; a device's
initial values (pushed in `__init__`) are adopted when the rig adds it;
input-only namespaces are not in the tree; a literal `TREE` adds to the
base tree. The blender starts `STOPPED`; a humidity demand puts it in
`BLEND`.

1. **core/signal**: typed `Reading` (+ `requested`, `at_limit`, `controller`), `dtype` beyond float, `Section`/tags, `Readings` protocol.
2. **core/device**: descriptor factories (`Node`, `Section`, `Demand`, `Output`, `Input`, `Config`, `For`), class-body collection, variables merge with references, `readable`/`writable`, base `Device` without `read`/`commit`/`observe`, `CommandSpec` with params, synthesised `set_`; `together`, `settings`, `state` removed.
3. **runtime**: store as the truth, inputs delivered by touching, command path (§5), live-limit clamp, program `group`, writer snapshot, `conditions` pushed by polling.
4. **db/recorder**: typed column + migration; history/export.
5. **server**: schema with links; one stream; `DeviceOut`/`SignalOut` reshaped; `/ws/writes`, `/ws/devices` removed.
6. **drivers**: controller sims/devices/integrations, linux package, humidity, stress/simulated rigs; `signals:` overrides for new addresses.
7. **book** reference.

Each step green: `make lint imports test` + pyright in `controller/`; linux and humidity suites; books `--strict`. UI last, separately.

## 9b. Composition (built 16 Sep, evening)

The rig can be built up while it runs: `Rig.add_link/remove_link/add_entry/
remove_device` with full teardown; `Rig.document()` renders the running rig
as a file (defaults left out; the humidity rig round-trips); every change is
a `rig_version` row in the store (migration 0008) via `rig.on_change`, and
a session records the version it started on. Routes in
`server/routes/composition.py`: links, devices, a whole document,
document/changes/versions/restore/save. `flyball-daemon` with no file
starts bare; `--resume` starts from the last change made through the API;
`<rig>.d/*.yaml` overlays saved by `save` load automatically. The UI's
add-device/add-link dialogs and Rig page are in progress.

## 10. State at 17 Sep 2026

Everything above is built and on `device-model-2` (last: 34d44f9). Also
since: `/ws/writes` and `/ws/devices` folded into `/ws/samples` (a
demand's reading carries requested / at_limit / controller; the frame
also carries polling runs; the stream cell merges a node's pushes within a
flush); a manual demand refused only while the controller is active;
`set_<path>` for computed trees on the instance; `Quantity` as
`Annotated` metadata (`Flow = Annotated[float, FLOW]`); tags on signals
from the rig file (`signals: {dry: {tags: {line: dry}}}`, namespace tags
apply below) and on sim ports; bearer token (`--token`), `--compose`,
`--drivers` + `/api/drivers`, `/api/probe`, `/api/links/{name}/query`;
the MCP server (the other session's) over all of it; the UI: rows by role
and dtype, command cards, section pivot, Add device / Add link, a Rig page
(document, changes, versions, restore, save, connect a model), token
entry, the tagged-union form.

Open:
- Two high-rate rigs (zoo, torrent) intermittently hit a React
  `removeChild` NotFoundError in the headless sweep; handed to the UI
  session.
- `WritePanel`'s "last reading" vs "committed" distinction, now that a
  demand's reading is its readback.
- Auth: `?token=` on a GET is logged where a header is not; `/mcp` shares
  the daemon's token. An opt-in for hot-attaching hardware exists
  (`--compose`); persistence of a hot-added device to the rig file is by
  `save` only.
