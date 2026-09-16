# Device model 2 — one store, descriptors, demands and commands

Outline, 16 Sep 2026. Follows the discussion after HANDOVER.md §5 and
supersedes its "alternative". Python first; the UI follows.

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

```python
    @command(commit=True, mode=Mode.FLOWS)
    def set_flows(self, dry: For[dry_flow], wet: For[wet_flow]) -> None:
        self._request = SupplyFlows(dry, wet)

    @command(commit=True, mode=Mode.STOPPED, owner_exempt=True)
    def stop(self) -> None: ...

    @command                       # maintenance: at once, no commit, no ownership check
    def restore(self) -> None: ...
```

`For[descriptor]` is `Annotated[<the descriptor's type>, descriptor]`, legal
in the class body because the name is already bound; resolved with
`get_type_hints(fn, include_extras=True, localns=vars(cls))`. Fallback: a
parameter named exactly like a descriptor links by name (`rig check` warns).

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

1. **core/signal**: typed `Reading` (+ `requested`, `at_limit`, `controller`), `dtype` beyond float, `Section`/tags, `Readings` protocol.
2. **core/device**: descriptor factories (`Node`, `Section`, `Demand`, `Output`, `Input`, `Config`, `For`), class-body collection, variables merge with references, `readable`/`writable`, base `Device` without `read`/`commit`/`observe`, `CommandSpec` with params, synthesised `set_`; `together`, `settings`, `state` removed.
3. **runtime**: store as the truth, inputs delivered by touching, command path (§5), live-limit clamp, program `group`, writer snapshot, `conditions` pushed by polling.
4. **db/recorder**: typed column + migration; history/export.
5. **server**: schema with links; one stream; `DeviceOut`/`SignalOut` reshaped; `/ws/writes`, `/ws/devices` removed.
6. **drivers**: controller sims/devices/integrations, linux package, humidity, stress/simulated rigs; `signals:` overrides for new addresses.
7. **book** reference.

Each step green: `make lint imports test` + pyright in `controller/`; linux and humidity suites; books `--strict`. UI last, separately.

## 10. Open

- Clamp-and-report vs reject (422) for a command past a limit (clamp, for consistency with `set`).
- `For[...]` vs by-name only, for the docs.
- Whether `last.<tag>` is recorded (P) or live only (R).
- Whether the killed UI edits (U4 value-or-generator) survive the wire change.
