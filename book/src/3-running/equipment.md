# Supported equipment

What a rig file can name today, how each is wired, and what is still to come.
Everything on this page is a `Device` like any other: once attached it has
routes, a schema, telemetry, CLI subcommands and a client method per command
with no further code.

## Formats

A rig is a file; a program is a file. Either may be written in any of:

| suffix | reads with | best for |
| --- | --- | --- |
| `.toml` | `tomllib` (stdlib) | **rig config** — typed leaves, no `yes`/`no`/`1e3` surprises |
| `.yaml` | PyYAML (a strict loader that rejects duplicate keys) | **rig config and programs** — deep envelopes read better, and a program is a list you read like a script |
| `.json` | stdlib | anything a machine wrote: a UI's save, a generated program |

Meaning is decided after parsing, by the same models in every case, so the
three are interchangeable; the choice is only what is pleasant to write. YAML
is the documented form for rig files.

```python
from flyball.runtime.config import load_rig
rig = load_rig("rig.yaml")
```

## A rig with nothing plugged in

`examples/simulated/` holds four rig files that run on a laptop — an oven
(first-order lag with dead time), a tank (an integrator against a drain), a
chiller (a reverse-acting loop), a three-zone tube furnace, and a bench of
scripted SCPI instruments — plus a `demo.py` that steps one through minutes
in a moment. They share two generic device tags any rig file may use:

| tag | is |
| --- | --- |
| `sim_plant` (a link) | one plant model — `model: lag`, `integrator` or `fopdt`, with gain, time constant, dead time, ambient, noise — shared by the devices that name it |
| `sim_daq` | reads the plant's outputs as signals: `ports: {name: port}` or `{name: {port, quantity, unit}}`, each port becoming one `[RP]` signal, advancing the plant by the time since the last read |
| `sim_drive` | drives the plant's inputs from the controller's demand: a plain port (drive itself, `of full` or a declared unit and `power_w`), or `demand: output` for a "smart" drive whose port is the plant's own output unit — `commit()` inverts the plant to find the input, clamped to `limits` |

`examples/simulated/oven.yaml`, quoted as the file actually is:

```yaml
name: oven

links:
  chamber:
    tag: sim_plant
    model: fopdt
    tau_s: 60.0        # the oven takes about a minute to respond
    dead_s: 5.0        # ... and five seconds before it starts to
    gain: 80.0         # full heater power (input 1.0) adds 80 °C over ambient
    ambient: 20.0      # the room; where it rests with the heater off
    initial: 20.0      # starts at room temperature
    noise: 0.05        # the thermocouple's noise, in °C
    seed: 1

devices:
  thermocouple:
    driver: sim_daq
    label: Oven thermocouple
    poll_s: 1.0
    config:
      link: chamber
      ports:
        temperature: { port: output, quantity: temperature, unit: "°C" }
    signals:
      temperature:
        range: [0, 120]
        precision: 2
        warn: [30, 90]     # outside: a warning (EPICS LOW/HIGH)
        alarm: [10, 110]   # outside: an alarm (EPICS LOLO/HIHI)
  heater:
    driver: sim_drive
    label: Oven heater
    config:
      link: chamber
      ports:
        drive: { port: input, demand: output, quantity: temperature, unit: "°C" }

controllers:
  heater.drive:
    signal: thermocouple.temperature
    law: { tag: PI, kp: 0.02, ki: 0.0005 }
    default: true
```

That is a complete rig: no Python, and every route, websocket, CLI
subcommand and schema is the same as for hardware. It is also what the
test suite runs the controller against.

## Links

A link is what a device talks over. Declare each once under `links` and
refer to it by name; a device may also embed its link inline. Every link
has a **fake** so a rig file runs without hardware.

| tag | talks | needs | fake |
| --- | --- | --- | --- |
| `visa` | SCPI over GPIB, USB-TMC, LAN (VXI-11/HiSLIP), serial — `TCPIP::192.168.1.20::INSTR`, `USB0::…::INSTR`, `ASRL/dev/ttyUSB0::INSTR` | `flyball[visa]` (pyvisa + pyvisa-py; no NI runtime) | `fake_text` |
| `serial` | a line-per-command serial device (Alicat, Sensirion, home-made boxes) | `flyball[serial]` | `fake_text` |
| `modbus_tcp` | Modbus TCP: PID controllers, MFCs, chillers, PLCs | `flyball[modbus]` | `fake_registers` |
| `modbus_rtu` | Modbus RTU over RS-485 | `flyball[modbus]` | `fake_registers` |

Each real link holds one lock, so two devices sharing an instrument never
interleave a query with a write.

## Devices

| tag | is | configured with |
| --- | --- | --- |
| `scpi` | one device, a query and/or a write template per signal | `channels: { voltage: { query: "MEAS:VOLT:DC?", unit: V } }` — `query` alone makes a signal `[RP]`, `write` alone `[W]`, both `[RPW]` |
| `modbus` | one device, a register per signal | `registers: { temperature: { address: 100, scale: 0.1, unit: "°C" } }` — `write: true` also makes it writable |

One `scpi` driver replaces the old `scpi_reader`/`scpi_actuator` pair;
likewise `modbus` replaces `modbus_reader`/`modbus_actuator`. For a generic
driver the *tree itself* is part of the driver's own config (`channels:`,
`registers:`) — the envelope's `signals:` then only overrides metadata
(range, precision, limits); a driver's config may never use an envelope key,
which is why the tree is not called `signals` there.

Register `kind` is one of `holding`, `input`, `coil` (an `input` register may
not be `write: true`), and `value = raw * scale`. SCPI replies are parsed as
a number by default (`+1.2E-3 V` → 0.0012); a device built in Python passes
`parse=` for an instrument that answers stranger text. `unit` is any symbol
the units table knows: `°C`, `mL/min`, `kPa`, `g/m³`, `N·m`.

Both drivers add commands for bring-up: `write`/`query` on `scpi` send any
text and return the raw reply, outside the declared tree. An instrument that
stops answering marks its device `offline` (an ERROR condition) until the
next successful read; polling continues.

## A rig file

`examples/simulated/bench.yaml`, quoted as the file actually is:

```yaml
name: bench

links:
  psu:
    tag: fake_text
    replies: { "MEAS:VOLT?": "11.98", "*IDN?": "Fake Instruments,PSU-1,0,1.0" }
  dmm:
    tag: fake_text
    replies: { "MEAS:VOLT:DC?": "+1.1980E+01", "MEAS:CURR:DC?": "0.250", "*IDN?": "Fake Instruments,DMM-1,0,1.0" }

devices:
  psu:
    driver: scpi
    label: Bench PSU
    poll_s: 1.0
    config:
      link: psu
      channels:
        set_voltage: { write: "SOUR:VOLT {value:.3f}", unit: V }   # [W]
        output_voltage: { query: "MEAS:VOLT?", unit: V }            # [RP]
    signals:
      set_voltage: { limits: [0, 30] }
      output_voltage: { precision: 3 }
  dmm:
    driver: scpi
    label: Bench DMM
    poll_s: 0.5
    config:
      link: dmm
      channels:
        voltage: { query: "MEAS:VOLT:DC?", unit: V }
        current: { query: "MEAS:CURR:DC?", unit: A }
    signals:
      voltage: { range: [0, 30], precision: 3 }
      current: { range: [0, 3], precision: 3 }

controllers:
  psu.set_voltage:
    signal: dmm.voltage
    law: { tag: PI, kp: 0.5, ki: 0.1 }
    default: true
```

Swap `tag: fake_text` for `tag: visa` with a `resource` string and the same
file runs against real hardware. `flyball-daemon bench.yaml` serves it;
`flyball rig check bench.yaml` validates it without serving: a wrong driver,
a link name that is not declared, a device name used twice, a writable
signal driven by two controllers, or a unit the table does not know fails
there rather than at start. `flyball rig schema > rig.schema.json` writes the
file's JSON schema, and a `# yaml-language-server: $schema=rig.schema.json`
first line points an editor at it.

A package that ships its own configs declares them once, and its tags are
valid in any file after `pip install`:

```toml
[project.entry-points."flyball.configs"]
keithley = "flyball_keithley.configs"
```

`flyball new NAME` writes `NAME.py`: a complete device driver with a tag,
ready to edit.

## Pushed devices

Anything that *pushes* values — an MQTT topic, an EPICS monitor, a websocket
— is a device with no period: attach it with `rig.add_device(device)` (no
`start_polling` call), and whenever a value arrives call
`rig.on_samples([sample])` from whatever thread produced it, as
`book/src/snippets/device.py`'s `PushedWeather` does:

```python
--8<-- "sensor.py:39:56"
```

`rig.on_samples` takes the rig's own lock, so it is safe from a serial
thread, a callback or a subscription — any thread but the one already
holding it.

## Wrapped instrument libraries

Two Python instrument libraries already carry hundreds of drivers, written
declaratively enough that one wrapper each turns *every* driver into a
flyball device. Use these before writing a driver.

| library | drivers | driver tag | what comes through |
| --- | --- | --- | --- |
| **QCoDeS** (`flyball[qcodes]`) | ~200: source-meters, lock-ins, cryogenic and quantum kit | `qcodes` (`flyball.integrations.qcodes`) | any gettable, numeric `Parameter` as a signal, with its unit read off the parameter unless overridden; a settable one is writable too |
| **PyMeasure** (`flyball[pymeasure]`) | ~150: Keithley, Agilent/Keysight, Lakeshore, Thorlabs, Anritsu, Oxford… | `pymeasure` (`flyball.integrations.pymeasure`) | the `measurement`/`control`/`setting` properties you name as signals, units read from their docstrings (`"in volts"` → V) with per-channel overrides |

Both wrappers declare their tree the same way the generic instrument drivers
do — under `channels:` in the driver's own config, each entry naming the
property to expose and whether to publish it:

```yaml
devices:
  smu:
    driver: qcodes
    instrument: qcodes.instrument_drivers.Keithley.Keithley2450
    channels:
      voltage: { property: source.voltage, publish: true }
```

```yaml
devices:
  smu:
    driver: pymeasure
    instrument: pymeasure.instruments.keithley.Keithley2400
    adapter: "GPIB::24"
    channels:
      voltage: { property: voltage, publish: true }
      bias: { property: source_voltage }
```

`instrument` is a dotted path to a class in a package you have installed;
nothing is fetched. What to know:

- The wrapped library owns the connection, so the link fakes are not
  involved; test against the library's own simulated instruments (QCoDeS
  `sims`, PyMeasure `FakeAdapter`), or point `instrument` at a fake class of
  your own, as the test suite does.
- Their calls block, like any polled device's; a slow instrument stretches
  a tick.
- A `publish: true` channel needs a getter -- a settable-only property is
  writable but not read back.
- Neither wrapper imports its library, so `import flyball` needs nothing;
  the extra is for the drivers themselves.

When a wrapped driver is slow, blocks the wrong way, or lacks a function you
need, a bespoke driver is a `Scpi` subclass or a plain `Device` with the
query table filled in.

## Already there, not via a rig file

| device | where | link |
| --- | --- | --- |
| Bluesky `NodeReadable`/`SignalMovable` over any flyball node or writable signal | `flyball.integrations.bluesky` | in-process |

`examples/humidity` (SHT4x humidity/temperature sensors, a dual-PWM pump
blender) is now an ordinary rig file — `rig.yaml` for the real hardware,
`sim.yaml` as a no-hardware overlay -- see the worked example in
[Boards and Linux I/O](boards.md) and the device model's own account in
`temp-docs/DEVICE-MODEL-PLAN.md` §2.

## Not yet

- **EPICS** (pyepics / p4p) and **OPC UA** (asyncua): the pushed-device path
  above is what they need; no adapter is written.
- **NI-DAQmx / LabJack** analogue I/O. (Bare Linux buses — I²C, SPI, GPIO,
  PWM, 1-Wire — are `flyball-linux`: [Boards and Linux I/O](boards.md).)
- Vendor packages with the query tables filled in (`flyball-keithley`,
  `flyball-alicat`). The entry-point discovery they would use exists; the
  packages do not. Until then the wrapped libraries above cover most bench
  kit.
- An instrument's own error queue (`SYST:ERR?`) surfacing as a condition.

## Getting data out

A recorded session exports as **Bluesky event-model documents** — the format
databroker, tiled and the facility analysis tools read: a `start`, a
`descriptor` per stream (one per device, one per controller, with each key's
units), an `event` per sample or tick, and a `stop`.

```
flyball sessions
flyball export 12 --out run12.jsonl        # {"name": ..., "doc": ...} per line
GET /api/sessions/12/documents             # the same, as [[name, doc], ...]
```

In Python, `flyball.db.documents.documents(store, session_id)` yields the
`(name, doc)` pairs a Bluesky callback or `databroker.v2` consumes directly.
