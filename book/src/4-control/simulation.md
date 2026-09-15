# Simulation

`flyball.sim` holds the pieces of a rig with no hardware. Nothing in it knows
what is simulated; an application composes them into its own simulator, and
the library's tests use them directly.

| | |
| --- | --- |
| `SteppedClock` | only moves when told to: `clock.advance(1.0)` |
| `Lag(tau_s, value, gain)` | a first-order plant; `step(u, dt_s)` holds `u` for `dt_s` seconds |
| `FunctionReader(name, {source: model})` | polls a function as if it were a sensor |
| `RecordingActuator(name)` | takes demands and remembers them |

## A loop with no hardware

[`oven.py`](../snippets/oven.py) is the whole thing:

```python
--8<-- "oven.py:52:60"
```

With a stepped clock, `rig.read(reader)` polls once and delivers on the
calling thread, so the loop ticks at exact instants and a test asserts on
what it did. Nothing sleeps.

## Testing a law

```python
from flyball.control import Loop, PI
from flyball.sim import Lag, RecordingActuator, SteppedClock

clock = SteppedClock(0)
plant = Lag(tau_s=10.0)
heater = RecordingActuator("heater")
loop = Loop(clock, heater, law=PI(kp=1.0, ki=0.1))
loop.regulate(1.0)
for _ in range(100):
    y = plant.step(heater.demands[-1], 0.1)
    loop.tick(sample(probe, TEMPERATURE, y, clock.advance(0.1)).reading(TEMPERATURE))
```

`Loop` needs no rig. Give it a clock and an actuator, tick it with readings,
and read `loop.correction`, `loop.demand` and `heater.demands` back.

## Serving a simulation

Swap the stepped clock for a real one and poll on a period, and the same rig
serves the HTTP API, the CLI and a UI. [`serve.py`](../snippets/serve.py)
does exactly that. It is the way to exercise a program, a layout or a client
on a laptop.
