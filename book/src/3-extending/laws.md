# Control laws

A `ControlLaw` turns `(elapsed, reading, setpoint)` into a **correction**:
the offset added to the feedforward's base to give the demand. Nine ship;
the set is open.

| tag | parameters | |
| --- | --- | --- |
| `open_loop` | — | correction is always zero |
| `P` | `kp` | proportional |
| `PI` | `kp`, `ki`, `tt`, `b` | proportional-integral, back-calculation anti-windup with tracking time `tt` |
| `PID` | `kp`, `ki`, `kd`, `tt`, `b`, `n` | derivative on the reading, not the error, so a setpoint step does not kick |
| `IMC` | `gain`, `tau`, `dead_time`, `lam`, `derivative`, `b` | a PID whose gains come from a first-order-plus-dead-time model by the IMC rule; retune by changing the model |
| `on_off` | `high`, `low`, `hysteresis` | a relay: `high` below the setpoint, `low` above, held inside the deadband; for an actuator that only switches |
| `smith` | `kp`, `ki`, `tt`, `gain`, `tau`, `dead_time`, `feedforward`, `b` | a PI on a reading with the dead time predicted out: the Smith predictor |
| `scheduled` | `points`, `tt`, `b`, `n` | a PID whose gains follow the setpoint: rows of `[setpoint, kp, ki, kd]`, interpolated, bumpless |
| `sliding` | `k`, `lam`, `boundary` | sliding mode on the surface `e + lam·∫e`, `±k` outside a boundary layer, proportional inside |

Gains are in parallel form: `kp·e + ki·∫e + kd·de/dt`. Tuning rules stated in
ideal form (`Kp`, `Ti`, `Td`) are converted once by `Gains.of_ideal`.

## Choosing

- **`PI` / `PID`** for most loops; the autotune rules produce their gains.
  `b` (setpoint weight, default 1) scales the setpoint in the proportional
  term only: under 1 it softens the kick a setpoint step gives the demand —
  the overshoot a step test shows — without changing how disturbances are
  rejected. Two-degree-of-freedom PID. `tt` omitted or 0 disables
  back-calculation anti-windup outright; a reasonable `tt` is about `Ti`
  (`kp/ki`) on `PI`, or `√(Ti·Td)` (`Td = kd/kp`) once a derivative term
  also acts, on `PID`/`Scheduled`. `PID`'s `n`, if given, filters the
  derivative through a first-order lag of time constant `1/n` before it is
  scaled by `kd` — raise it for less filtering; omitted, the derivative is
  the raw rate on the reading.
- **`IMC`** when you have the plant's model (from a step test or the
  identifier) and would rather keep the model in the rig file than gains
  derived from it. `lam` is the closed-loop time constant asked for; the
  default is about as fast as the plant already is.
- **`on_off`** for a contactor, a solenoid, anything that cannot modulate,
  under `feedforward: none` so the correction is the whole demand. Wider
  `hysteresis` switches less and holds less tightly; the dead time adds to
  the swing beyond the band.
- **`smith`** when the dead time is comparable to the time constant, so a
  PI tuned for the whole plant would be sluggish. The law runs its own model
  of what the plant will have done once the dead time passes and lets the PI
  be tuned for the lag alone. It never sees the demand's feedforward base,
  so `feedforward` says what the controller's feedforward hands on per unit
  of setpoint: 1 under the default `setpoint` feedforward, 0 under `none`.
- **`scheduled`** when one tuning is sluggish at one end of the range and
  rings at the other: a heater whose losses grow with temperature, a valve
  nonlinear in its travel. When `ki` changes the integrator is rescaled so
  its contribution does not jump.
- **`sliding`** for robustness to a gain you do not know well: on the
  surface the error decays as a first-order system of time constant
  `1/lam`, whatever the plant's gain within what `k` supplies. The cost is
  chattering; `boundary` (a few times the reading's noise) turns the switch
  into a saturated PI inside the layer. Better suited to a power stage than
  a pump.

## What a law provides

```python
class MyLaw(ControlLaw, tag="mine"):
    def __init__(self, gain: float) -> None: ...
    def step(self, elapsed: float, reading: float, setpoint: float,
             last_applied: float | None = None) -> float: ...
    def reset(self) -> None: ...
    def resume(self, reading: float, setpoint: float, correction: float) -> float: ...
```

- `step` is called once per tick with seconds since the law's own start.
  `last_applied` is the correction the target actually delivered last
  tick, or `None`.
- `reset` clears memory: a cold start.
- `resume` seeds memory so the next `step` reproduces `correction`, and
  returns what it managed. The default is a cold start; a law with an
  integral overrides it. See [Handover](../6-internals/controller.md#handover).

## What subclassing generates

Three pydantic models, from the class itself, so a law is described once:

- `config` — one field per `__init__` parameter, plus `tag`. Builds the law.
- `state` — one field per name in `_state_fields`, merged up the MRO.
- `view` — both flattened, round-tripping through `build()`.

`MyLaw.config` is the model class; `law.config` is that law's values. A new
law gains a wire schema this way, but nothing selects it by tag until it is
registered: add `catalog.register_law(MyLaw)` to your package's
`register(catalog)` -- the same `flyball.configs` entry point a device or
link registers through, see [Packaging](packaging.md). Once registered, it
is selectable by tag in a file or a request, and appears in the generated
command form.

## Tunings

A `Tuning` is a named law config. The rig holds a registry (`rig.tunings`),
and the store keeps versions (`PUT /api/history/tunings/{name}`). A
`regulate` step may name one, so a program says `tuning: fitted` rather than
carrying gains.
