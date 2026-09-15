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
    y = plant.drive(heater.demands[-1], 0.1)
    loop.tick(sample(probe, TEMPERATURE, y, clock.advance(0.1)).reading(TEMPERATURE))
```

`Loop` needs no rig. Give it a clock and an actuator, tick it with readings,
and read `loop.correction`, `loop.demand` and `heater.demands` back.

## Serving a simulation

Swap the stepped clock for a real one and poll on a period, and the same rig
serves the HTTP API, the CLI and a UI. [`serve.py`](../snippets/serve.py)
does exactly that. It is the way to exercise a program, a layout or a client
on a laptop.

## A simulation from a file

A rig file whose links are all `sim_*` or `fake_*` is a simulation, and the
daemon treats it as one: the rig gets a clock whose speed can change, and
`/api/sim` (and `flyball sim`) exposes the knobs.

```toml
name = "oven"
clock = { speed = 60 }        # a simulated minute per second; refused on real hardware

[links.chamber]
tag = "sim_plant"
kind = "fopdt"
tau_s = 60
dead_s = 5
gain = 80
ambient = 20
noise = 0.05
```

```
flyball-daemon oven.toml
flyball sim                          # clock 60x; chamber fopdt tau_s=60 ...
flyball sim clock 200                # faster, from now on; time stays continuous
flyball sim set chamber tau_s=30 noise=0.2
flyball sim reset chamber 50         # put the output at 50, keep running
flyball sim config                   # the file as it now stands
flyball sim save                     # write it back (or `save other.yaml`)
```

Parameters change *while the plant runs* — its state is kept, as a real
plant's would be — so you can watch the loop cope with a plant that just got
faster. `kind` cannot change without a restart. `save` rewrites the rig file
with the new parameters and clock speed, in the format its suffix names, so
the next `flyball-daemon` starts from what you settled on. Unsaved changes
are listed by `flyball sim`.

`flyball sim` shows each parameter beside what it stands for on the running
rig: `initial_c  20  (now 604 / 609 / 601 / 578)`, `noise  0.3  (observed
0.31)`. The pairing is declared on the config field --
`Field(json_schema_extra={"live": "outputs.*"})` -- and the path points into
the plant as `/api/sim` reports it: `output` or `outputs.*` for where the
quantity is now, `stats.noise` for the observed noise per port; a `*`
segment fans out over every key at that level. A field with no `live` is a
parameter (`tau_s`, `coupling_w_per_k`), and the schema says so by its
absence. `/api/sim` carries the resolved values as `live`, the paths as
`links`, what each reader last delivered (value, unit, precision, reader,
age) as `readings`, `stats.noise` and `stats.rate_per_min` per port over the
last minute of readings, and `clock.measured`: rig time against wall time
since the last poll, so a rig that cannot keep up with `speed` shows it. The
same key works on any device config -- `sim_actuator.limits` is `live:
"state.input"`, so a clamp biting shows beside the limits in the device's
panel -- and rides along in the device's schema route.

`clock = { stepped = true }` gives a clock that moves only when stepped:
`POST /api/sim/clock/step` (`flyball sim step 60`), or a program's own holds
and ramps. Polled readers are scheduled *on* the clock rather than on
threads, so stepping runs every poll due on the way, in order; a hold steps
the clock past itself with the physics integrated underneath. A two-hour
firing on the furnace example runs that way in a quarter of a second, and
identically every time — which is how it is tested.

## A multi-port plant

`sim_plant` has one input and one output. `sim_furnace` has several of
each — `heater1..N` in, `zone1..N` and `sample` out — and a `sim_reader` or
`sim_actuator` names which with `port`. The plant steps once per instant
however many readers ask, so the zones are consistent. It is the example to
read for a plant of your own with more than one port: implement the
`MultiPlant` protocol (inputs, output names, `advance(time_ns)`,
`feedforward`, `inverse_feedforward`) and a config with `retune`, and the
readers, actuators, `/api/sim` and `flyball sim` all work on it.

`flyball zone3 fail` opens a simulated thermocouple: the reader goes
`offline`, an event is raised, and what the loop does next is yours to
watch. `restore` mends it.

### What speed does

Everything that waits — reader polling, a program's holds, a signal's
timeout, the "slow reader" judgement — goes through the rig's clock, so at
60× a reader on `period_s = 1` reads sixty times a second and a ten-minute
hold takes ten seconds. The ceiling is the slowest real step: when polls
start to overlap, the reader's `slow` condition appears. A few hundred× is
comfortable on a laptop for a handful of plants; beyond that, step.
