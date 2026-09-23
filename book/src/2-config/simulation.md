# Simulation

`flyball-sim` (`flyball_sim`) holds the pieces of a rig with no hardware.
Nothing in it knows what is simulated; an application composes them into
its own simulator, and the library's tests use them directly. It is its
own top-level package (a sibling of `engine/`, not nested under it), with
zero third-party dependencies of its own -- `flyball[server]` pulls it in by
default, so `pip install flyball[server]` alone can already run a simulated rig.

| | |
| --- | --- |
| `SteppedClock` | only moves when told to: `clock.advance(1.0)` |
| `ScaledClock(speed)` | real time × `speed`, changeable while running |
| `Lag(tau_s, value, gain, ambient)` | a first-order plant; `advance(dt_s)` holds `input` for `dt_s` seconds |
| `Fopdt(tau_s, dead_s, gain)` | a `Lag` whose input arrives `dead_s` late |
| `Integrator(gain, leak)` | `dy/dt = gain·u − leak·y`: a tank against a drain |
| `Noisy(plant, sigma)` | any plant, read through Gaussian noise; the plant itself stays clean |
| `MultiPlant` | the protocol several named ports implement, so a `sim_daq`/`sim_drive` can share one plant across devices |
| `SimDaq` / `SimDrive` | generic devices: read a plant's outputs as `[RP]` signals, drive its inputs from `[W]` ones |

`SimDaq` and `SimDrive` (`flyball_sim.devices`) are what a rig file declares
under `driver: sim_daq` / `driver: sim_drive`; they replace the old
`sim_reader`/`sim_actuator`, one device per plant side rather than one per
port. A `Lag`/`Fopdt`/`Integrator` is a bare `Plant` (one `input`, one
`output`); a `MultiPlant` an application writes itself, or `examples/furnace`'s
worked `Furnace` (§ Applications' own plants, below), has several of each,
named.

## A controller with no hardware

[`oven.py`](../snippets/oven.py) builds a rig with a `Probe` (`[RP]`,
reading a `Lag`), a `Heater` (`[W]`, driving it) and the default controller
between them, then runs it with nothing polling:

```python
--8<-- "oven.py:main"
```

With a stepped clock, `rig.read(node, fresh=True)` reads once and delivers
on the calling thread, so the controller ticks at exact instants and a test
asserts on what it did. Nothing sleeps: `clock.advance(1.0)` runs the
`Lag`'s own `120` seconds of physics in however long the arithmetic takes.

## Testing a law without a rig

A `Controller` needs bound signals — `Access.W` on the output, `Access.P` on
the measured signal — but not a running `Rig`: built with no `write`
callback, it records its output on itself (`controller.output`) instead of committing
anything, which is enough to drive a bare plant by hand:

```python
from flyball.control import PI
from flyball.model.controller import Controller
from flyball.foundation import Access, Quantity, Reading, Role, SignalSpec
from flyball.foundation.device.device import Device
from flyball.foundation.quantities.si import Celsius
from flyball_sim import Lag, SteppedClock

TEMPERATURE = Quantity("temperature", Celsius)


class Bench(Device):
    TREE = (
        SignalSpec(name="reading", quantity=TEMPERATURE, access=Access.RP),
        SignalSpec(name="heater", quantity=TEMPERATURE, access=Access.RPW, role=Role.DEMAND),
    )


bench = Bench("bench")
plant = Lag(tau_s=10.0)
clock = SteppedClock(0)
controller = Controller(
    clock,
    bench.signals["heater"],
    bench.signals["reading"],
    law=PI(kp=1.0, ki=0.1),
    write=lambda value: plant.drive(value, 0.1),
)
controller.regulate(1.0)
for _ in range(100):
    clock.advance(0.1)
    controller.tick(Reading(bench.signals["reading"], clock.now_ns(), plant.output))
```

`controller.correction`, `controller.output` and `plant.output` (or, with a
`write` that records instead of driving, the values it was handed) are what
a law's own test asserts on. No `Rig`, no device tree beyond the two signals
a controller needs.

## Serving a simulation

Swap the stepped clock for a real one and poll on a period, and the same rig
serves the HTTP API, the CLI and a UI. [`serve.py`](../snippets/serve.py)
does exactly that — `rig.start_polling(rig.devices["probe"])`, then
`rig.controllers.resolve(None).regulate(100.0)` before handing the rig to
the server. It is the way to exercise a program, a layout or a client on a
laptop.

## A simulation from a file

A rig file whose links are all `sim_*`/`fake_*` is a simulation, and the
runner treats it as one: the rig gets a clock whose speed can change, and
`/api/sim` (and `flyball sim`) exposes the knobs. A `ScaledClock` starts at
`max(real now, the store's last session end)`, so a restart after running
faster than real time cannot land behind where the last run left off; a
stepped clock, only moving when told, needs no such seed.

```yaml
# examples/simulated/oven.yaml
name: oven

links:
  chamber:
    type: sim_plant
    model: fopdt          # renamed from `kind`, so it doesn't read as the link's own discriminator
    tau_s: 60.0            # the oven takes about a minute to respond
    dead_s: 5.0             # ... and five seconds before it starts to
    gain: 80.0              # full heater power (input 1.0) adds 80 °C over ambient
    ambient: 20.0            # the room; where it rests with the heater off
    initial: 20.0
    noise: 0.05              # the thermocouple's noise, in °C
    seed: 1

devices:
  thermocouple:
    driver: sim_daq
    label: Oven thermocouple
    poll_s: 1.0
    link: chamber
    ports:
      temperature: { port: output, quantity: temperature, unit: "°C" }
    signals:
      temperature: { range: [0, 120], precision: 2, warning: [30, 90], alarm: [10, 110] }
  heater:
    driver: sim_drive
    label: Oven heater
    link: chamber
    ports:
      drive: { port: input, demand: output, quantity: temperature, unit: "°C" }

controllers:
  heater.drive:
    measured: thermocouple.temperature
    law: { type: PI, kp: 0.02, ki: 0.0005 }
    default: true
```

`sim_plant`'s own field for which model it is (`lag`, `integrator`, `fopdt`)
is `model:`, not `kind:` — freeing `kind` from meaning two different things
(the link's own `type: sim_plant` is the discriminator flyball reads; `model`
is one of `sim_plant`'s own parameters). `heater`'s port is declared
`demand: output`: a demand is the temperature to hold, in °C, and `commit()`
inverts the plant's static model to find the drive fraction, rather than
leaving that job to the controller's feedforward — so `heater.drive`'s law
can be tuned in plain °C-per-°C and the default `identity` feedforward hands
the target the setpoint untouched.

Several `sim_daq`s may read one bare `sim_plant`: it is stepped once per
instant, by whichever reads first, so its time never runs faster than the
clock. A `fopdt` plant's delayed input takes effect at the instant it
arrives, so its trajectory does not depend on how often it is read.

```
flyball-runner oven.yaml
flyball sim                          # clock 1x; chamber fopdt tau_s=60 ...
flyball sim clock 200                # faster, from now on; time stays continuous
flyball sim set chamber tau_s=30 noise=0.2
flyball sim reset chamber 50         # put the output at 50, keep running
flyball sim config                   # the file as it now stands
flyball sim save                     # write it back (or `save other.yaml`)
```

Parameters change *while the plant runs* — its state is kept, as a real
plant's would be — so you can watch the controller cope with a plant that
just got faster. `model` cannot change without a restart. `save` rewrites
the rig file with the new parameters and clock speed, in the format its
suffix names, so the next `flyball-runner` starts from what you settled on.
`save` replaces the file atomically (a unique temp file, fsynced, then
renamed).
Unsaved changes are listed by `flyball sim` (`flyball sim show`, its full
name; bare `flyball sim` is the same command).

`flyball sim` shows each parameter beside what it stands for on the running
plant: `initial 20 (now 60.4)`, `noise 0.05 (observed 0.051)`, and, for
every signal a `sim_daq` reads off it, the last value delivered, its device
and how old it is. The pairing is declared on the config field --
`Field(json_schema_extra={"live": "output"})` on `sim_plant`'s `ambient`,
`initial` and `noise`, `"outputs.*"` on `examples/furnace`'s `sim_furnace`
`ambient_c` and `initial_c` -- and the path points into the plant as `/api/sim` reports it:
`output`/`outputs.*` for where the quantity is now, `stats.noise` for the
observed noise per port. A field with no `live` is a parameter (`tau_s`,
`coupling_w_per_k`), and the schema says so by its absence. `GET /api/sim`
carries the resolved values as each plant's `live`, the paths as `links`,
what each `sim_daq` last delivered (value, unit, precision, device, port,
age) as `readings`, and `stats.noise`/`stats.rate_per_min` per signal over
the recent readings -- see [the API reference](../4-server/api.md#simulation)
for the full shape and the rest of `/api/sim/*`.

`clock: { stepped: true }` gives a clock that moves only when stepped:
`POST /api/sim/clock/advance` (`flyball sim advance 60`), or a program's own waits
and ramps. Polled devices are scheduled *on* the clock rather than on
threads, so stepping runs every poll due on the way, in order; a wait steps
the clock past itself with the physics integrated underneath. A two-hour
firing on the furnace example runs that way in a quarter of a second, and
identically every time — which is how it is tested.

## A multi-port plant

`sim_plant` has one input and one output, named `input`/`output`; a bare
`sim_daq`/`sim_drive` port spells out `quantity`/`unit` since the plant
cannot say what they are. `examples/furnace`'s `sim_furnace` (`Furnace`) has
several of each -- `heater1..N` in, `zone1..N` and `sample` out -- and knows
they are temperatures and watts (its own `output_quantity`/`input_quantity`
hooks, duck-typed rather than part of the `MultiPlant` protocol so an
ordinary `MultiPlant` need not implement them), so a `sim_daq` reading it
needs no `quantity`/`unit`. The plant steps once per instant however many
devices ask, so the zones stay consistent whatever order they are read in:

```yaml
# examples/furnace/rig.yaml
links:
  tube:
    type: sim_furnace
    zones: 3
    power_w: [2500, 6000, 2000]
    coupling_w_per_k: 5.0
    sample_zone: 2
    sensor_lag_s: 3.0
    noise: 0.3
    seed: 7

devices:
  furnace:                               # the thermocouples: furnace.zone1..3, furnace.sample [RP] °C
    driver: sim_daq
    poll_s: 1.0
    link: tube
    ports: { zone1: zone1, zone2: zone2, zone3: zone3, sample: sample }
  heaters:                               # heaters.heater1..3 [W] in watts, limits 0..power_w
    driver: sim_drive
    link: tube
    ports: { heater1: heater1, heater2: heater2, heater3: heater3 }
```

`examples/furnace`'s `sim_furnace` is the example to read for a plant of
your own with more than one port: implement the `MultiPlant` protocol
(`inputs`, `output_names`, `output(port)`, `advance_to(time_ns)`,
`feedforward(port, demand)`, `inverse_feedforward(port, drive)`) and a
`Config` with `retune`, in a package of your own -- registering its type
through the `flyball.configs` entry point, the way `examples/furnace`'s
`pyproject.toml` registers `sim_furnace` -- and `sim_daq`, `sim_drive`,
`/api/sim` and `flyball sim` all work on it unchanged.

`SimDaq` carries `fail`/`restore` commands, and `SimDrive` a `disturb` one,
for exercising fault handling:

```
flyball invoke furnace fail signal=zone3     # opens the thermocouple: reads on it raise HardwareError
flyball invoke furnace restore signal=zone3  # mends it; a command on an offline device polls it again
```

`fail` raises `HardwareError` on the next read of that signal until
`restore` -- what a controller does about a source going offline is the
thing to watch. `SimDrive.disturb(signal, offset)` kicks the plant's drive
on `signal`'s port by `offset` (in the signal's own unit) without touching
the demand that set it there -- a door opened, a leak -- so the plant moves
and the controller only finds out once the reading does. The kick persists
across later commits -- a regulated loop's own demand does not wipe it out,
so it can test disturbance rejection -- until a fresh `disturb` re-sets it,
`offset=0.0` clears it, or, with `duration_s` given, it expires on its own.
`offset` must be finite: NaN or infinity is refused.

## The overlay pattern

A rig file's links, not its devices, are what changes between real hardware
and simulation: `furnace.yaml` above declares `furnace` and `heaters` with
`driver: sim_daq`/`sim_drive` directly on a `sim_furnace` link named `tube`,
so every address (`furnace.zone1`, `heaters.heater1`), controller
(`heaters.heater1`) and program is exactly what a real thermocouple DAQ and
SSR bank would declare under the same device names -- when such drivers
exist, this file splits into a real `furnace.yaml` (`driver: eurotherm_daq`,
`driver: ssr_bank`, ...) plus a `sim.yaml` overlay that swaps only the
`links:` and the two devices' `driver`/`config`, and every dashboard and
recorded session carries over unchanged (see [Overlays: real vs
simulated](../3-extending/model.md#overlays-real-vs-simulated)).

`examples/humidity` already has both halves:

```yaml
# examples/humidity/rig-multi-sensor.yaml (real hardware)
devices:
  hum_sensors: { driver: sht4x_set, link: i2c1, sensors: { chamber: {...}, dry: {...}, wet: {...} } }
  blender: { driver: dual_pump_blender, link: pwm0, ... }
```

```yaml
# examples/humidity/sim.yaml (overlay: no hardware)
links:
  i2c1: null                             # deletes the real link
  pwm0: null
  chamber: { type: sim_humidity_chamber, dry: 10.0, wet: 90.0, tau_s: 45.0, ... }
devices:
  hum_sensors:
    driver: sim_daq
    link: chamber
    ports:
      chamber.humidity: { port: chamber_humidity, quantity: humidity, unit: "%RH" }
      dry.humidity: { port: dry_humidity, quantity: humidity, unit: "%RH" }
      # ... one entry per signal; a dotted key puts it in a namespace
  blender:
    driver: sim_drive
    inputs: null
    link: chamber
    ports: { humidity: { port: wet_fraction, quantity: humidity, unit: "%RH", limits: [0, 100] } }
```

`flyball-runner rig.yaml` runs the hardware; `flyball-runner rig.yaml
sim.yaml` overlays it -- later file wins, `null` deletes a key, so `i2c1`
and `pwm0` vanish and `chamber` (a `MultiPlant` the humidity example writes
itself, `sim_humidity_chamber`, implementing the same protocol as
`sim_furnace`) takes their place. `hum_sensors`' dotted `ports:` keys
(`chamber.humidity`, `dry.humidity`, ...) mirror the namespaces
`sht4x_set`'s own config declares, address for address; `blender` is
reduced to its one `[W]` target, `humidity` -- the manual flow/effort
signals `dual_pump_blender` declares have no `sim_drive` analogue, since
there is no pump arithmetic to read back, and are simply absent from the
simulated rig. `blender.humidity`'s controller entry needs no override in
`sim.yaml` at all: same source address, same target address, same law.

## What speed does

Everything that waits -- a device's polling, a program's `wait` steps, a
signal's timeout, the "slow device" condition -- goes through the rig's
clock, so at 60× a device on `poll_s: 1` reads sixty times a second and a
ten-minute `wait` takes ten seconds. The ceiling is the slowest real step: when polls start to
overlap, the device's `slow` condition appears. A few hundred× is
comfortable on a laptop for a handful of plants; beyond that, step.
