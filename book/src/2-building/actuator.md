# Writing an actuator

An actuator is a device with one more method. The minimum is a class, a
`set_demand`, and a `state`:

```python
--8<-- "oven.py:15:31"
```

## `set_demand`

```python
def set_demand(self, demand: float) -> float | None: ...
```

*Given a demand in the controlled quantity's units, do what you can, and
report what you expect to deliver in the same units.* `None` means "I cannot
say".

The return value matters. The loop compares it with what it asked for and
feeds the difference to the law as anti-windup: when the actuator is railed,
the law's integral stops accumulating a demand that can never be met. An
actuator that returns `None` gets no anti-windup rather than a wrong one.

`demand_unit` names the unit `set_demand` takes: the loop's measurand, or
`None` for a unitless demand.

`set_demand` is called on the control thread, every tick. Keep it cheap. Slow
I/O belongs in `apply`, which the rig calls once per delivery after every
loop has ticked; the default does nothing.

## The three tiers

A device describes itself in config, settings and state. Each is a type the
device declares by annotating a property, and each is checked on subclassing:
it must derive from the right base and pydantic must be able to describe it.

```python
--8<-- "device.py:11:32"
```

- **Config** is a pydantic model, because it is *what builds the device*: it
  arrives from a file or a request, is validated, and `build()`s. Give it a
  `tag` so a file can select it by name. See [Config and build](config.md).
- **Settings** and **state** are frozen dataclasses. Settings change by
  command; state changes every tick.

The device returns them from properties:

```python
--8<-- "device.py:55:68"
```

## Commands

A method marked `@command` is an action the device offers. Its signature *is*
the command: the server derives the request model from the parameters, the
CLI derives flags, a program derives a step.

```python
--8<-- "device.py:70:79"
```

Every parameter and the return type must be describable by pydantic; this is
checked when the class is defined, not on the first request. The tag defaults
to the method name; `@command(tag="off")` overrides it. `schema` is reserved
as a route segment.

## Conditions

Something true of the device *now* — railed, offline, overdriven — is a
`Condition` in its state:

```python
--8<-- "device.py:63:68"
```

It appears while it holds and is gone when it clears. A client joining late
sees the present, not a log. `kind` is stable and machine-readable; `level`
uses `logging`'s numbers.

For a moment rather than a state — a request clamped, a retry that worked —
a device with a rig in hand records an event instead:

```python
rig.event(Level.WARNING, "actuator", self.name, "clamped", f"{demand} limited to {limit}")
```

The rig keeps the last few hundred, streams them on `/ws/events`, and writes
them to the session when recording. The rig raises its own: a reader going
offline or reading slowly, a program step failing or a wait timing out.

## What you get

Once attached to a rig (`rig.add_actuator(heater)` or via `attach_loop`), with
no further code:

```
GET  /api/actuators/heater            config, settings, state
GET  /api/actuators/heater/schema     the three schemas and every command's
POST /api/actuators/heater/set_limit  {"limit": 0.5}
POST /api/actuators/heater/off

flyball heater
flyball heater set_limit 0.5
flyball heater off
```

and one frame per change on `/ws/actuators`.

## Checklist

- `set_demand` returns what it expects to deliver, or `None`.
- `demand_unit` is set.
- `config`, `settings`, `state` return the declared types.
- Anything slow is in `apply`, not `set_demand`.
- Conditions cover every way the device can fail to do what it was asked.
