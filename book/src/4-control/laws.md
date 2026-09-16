# Control laws

A `ControlLaw` turns `(elapsed, reading, setpoint)` into a **correction**:
the offset added to the setpoint to give the demand. Four ship; the set is
open.

| tag | parameters | |
| --- | --- | --- |
| `open_loop` | — | correction is always zero |
| `P` | `kp` | proportional |
| `PI` | `kp`, `ki`, `tt` | proportional-integral, back-calculation anti-windup with tracking time `tt` |
| `PID` | `kp`, `ki`, `kd`, `tt` | derivative on the reading, not the error, so a setpoint step does not kick |

Gains are in parallel form: `kp·e + ki·∫e + kd·de/dt`. Tuning rules stated in
ideal form (`Kp`, `Ti`, `Td`) are converted once by `Gains.of_ideal`.

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
  integral overrides it. See [Handover](loop.md#handover).

## What subclassing generates

Three pydantic models, from the class itself, so a law is described once:

- `config` — one field per `__init__` parameter, plus `tag`. Builds the law.
- `state` — one field per name in `_state_fields`, merged up the MRO.
- `view` — both flattened, round-tripping through `build()`.

`MyLaw.config` is the model class; `law.config` is that law's values. A new
law therefore self-registers, gains a wire schema, is selectable by tag in a
file or a request, and appears in the generated command form, with no further
code.

## Tunings

A `Tuning` is a named law config. The rig holds a registry (`rig.tunings`),
and the store keeps versions (`PUT /api/history/tunings/{name}`). A
`regulate` step may name one, so a program says `tuning: fitted` rather than
carrying gains.
