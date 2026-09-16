# Supported equipment

What a rig file can name today, how each is wired, and what is still to come.
Everything on this page is a `Reader` or an `Actuator` like any other: once
attached it has routes, a schema, telemetry, CLI subcommands and a client
method per command with no further code.

## Formats

A rig is a file; a program is a file. Either may be written in any of:

| suffix | reads with | best for |
| --- | --- | --- |
| `.toml` | `tomllib` (stdlib) | **rig config** — typed leaves, no `yes`/`no`/`1e3` surprises |
| `.yaml` | PyYAML | **programs** — a list you read like a script |
| `.json` | stdlib | anything a machine wrote: a UI's save, a generated program |

Meaning is decided after parsing, by the same models in every case, so the
three are interchangeable; the choice is only what is pleasant to write.

```python
from flyball.runtime.config import load_rig
rig = load_rig("rig.toml")
```

## A rig with nothing plugged in

`examples/simulated/` holds three rig files that run on a laptop — an oven
(first-order lag with dead time), a tank (an integrator against a drain),
and a bench of scripted SCPI instruments — and a `demo.py` that steps one
through minutes in a moment. They use two device tags any rig file may:

| tag | is |
| --- | --- |
| `sim_plant` (a link) | one plant model — `lag`, `integrator` or `fopdt`, with gain, time constant, dead time, ambient, noise — shared by the devices that name it |
| `sim_reader` | reads the plant's output as a measurand with a unit, range and precision, advancing the plant by the time since the last read |
| `sim_actuator` | drives the plant's input from the loop's demand, clamped to `limits`: in the output's unit through the plant's feedforward (a packaged controller), or as the drive itself in `of full` or a power (`unit = "W"`, `power_w`); commands `set_limits` and `disturb` |

```toml
[links.chamber]
tag = "sim_plant"
kind = "fopdt"
tau_s = 60.0
dead_s = 5.0
gain = 80.0
ambient = 20.0
initial = 20.0
noise = 0.05

[[readers]]
period_s = 1.0
[readers.device]
tag = "sim_reader"
name = "thermocouple"
link = "chamber"
measurand = "temperature"
unit = "°C"
range = [0, 120]

[[actuators]]
tag = "sim_actuator"
name = "heater"
link = "chamber"

[[loops]]
channel = "thermocouple.temperature"
actuator = "heater"
law = { tag = "PI", kp = 0.02, ki = 0.0005 }
default = true
```

That is a complete rig: no Python, and every route, websocket, CLI
subcommand and schema is the same as for hardware. It is also what the
test suite runs the loop against.

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

Each real link holds one lock, so a reader and an actuator sharing an
instrument never interleave a query with a write.

## Devices

| tag | is | configured with |
| --- | --- | --- |
| `scpi_reader` | a `Reader`: one source, a query per measurand | `measurands = { voltage = { query = "MEAS:VOLT:DC?", unit = "V", range = [0, 30], precision = 3 } }` |
| `scpi_actuator` | an `Actuator`: one command with the demand formatted in | `command = "SOUR:VOLT {value}"`, `demand_unit = "V"`, optional `readback = "MEAS:VOLT?"` |
| `modbus_reader` | a `Reader`: a register per measurand | `registers = { temperature = { address = 100, scale = 0.1, unit = "°C" } }` |
| `modbus_actuator` | an `Actuator`: writes one register | `output = { address = 200, scale = 10, kind = "u16" }` |

Register `kind` is one of `u16`, `s16`, `u32`, `s32`, `f32` (two-word kinds
take a `word_order`), and `value = raw * scale + offset`. SCPI replies are
parsed as a number by default (`+1.2E-3 V` → 0.0012); a subclass passes
`parse=` for anything stranger. `unit` is any symbol the units table knows:
`°C`, `mL/min`, `kPa`, `g/m³`, `N·m`.

Both readers add commands: `identify` (`*IDN?`) and `query` on SCPI; both
actuators report `demand` and, where available, the instrument's own
readback in `state`. An instrument that stops answering marks its reader
`offline` (an ERROR condition) until the next successful read; polling
continues.

## A rig file

```toml
name = "bench"

[links.bench]
tag = "visa"
resource = "TCPIP::192.168.1.20::INSTR"

[links.chiller]
tag = "modbus_tcp"
host = "192.168.1.30"

[[readers]]
period_s = 0.5
[readers.device]
tag = "scpi_reader"
name = "dmm"
link = "bench"
[readers.device.measurands.voltage]
query = "MEAS:VOLT:DC?"
unit = "V"
range = [0, 30]
precision = 3

[[readers]]
period_s = 2
[readers.device]
tag = "modbus_reader"
name = "bath"
link = "chiller"
registers = { temperature = { address = 100, scale = 0.1, unit = "°C" } }

[[actuators]]
tag = "scpi_actuator"
name = "psu"
link = "bench"
command = "SOUR:VOLT {value}"
demand_unit = "V"
readback = "MEAS:VOLT?"

[[loops]]
channel = "dmm.voltage"
actuator = "psu"
law = { tag = "PI", kp = 0.2, ki = 0.05 }
default = true
```

Swap `tag = "visa"` for `tag = "fake_text"` with a `replies` table and the
same file runs on a laptop. `flyball-daemon bench.toml` serves it;
`flyball rig check bench.toml` validates it without serving: a wrong tag, a
link name that is not declared, a device name used twice, an actuator driven
by two loops, or a unit the table does not know fails there rather than at
start. `flyball rig schema > rig.schema.json` writes the file's JSON schema,
and a `#:schema rig.schema.json` first line points a TOML editor at it.

A package that ships its own configs declares them once, and its tags are
valid in any file after `pip install`:

```toml
[project.entry-points."flyball.configs"]
keithley = "flyball_keithley.configs"
```

`flyball new actuator NAME` (or `new reader`) writes a complete device with
a tag to start from.

## Pushed sources

Anything that *pushes* values — an MQTT topic, an EPICS monitor, a websocket
— is a plain `Reader` with no `read` override: the callback calls
`reader.push(source, values, time_ns)` and the sample reaches the rig at
once. Attach it with `rig.start_reader(reader)` (no period).

## Wrapped instrument libraries

Two Python instrument libraries already carry hundreds of drivers, written
declaratively enough that one wrapper each turns *every* driver into a
flyball reader or actuator. Use these before writing a driver.

| library | drivers | wrapper | what comes through |
| --- | --- | --- | --- |
| **QCoDeS** (`flyball[qcodes]`) | ~200: source-meters, lock-ins, cryogenic and quantum kit | `flyball.integrations.qcodes` | every numeric, gettable `Parameter` as a measurand *with its unit, label and validator bounds*; any settable one as an actuator or via the `set` command |
| **PyMeasure** (`flyball[pymeasure]`) | ~150: Keithley, Agilent/Keysight, Lakeshore, Thorlabs, Anritsu, Oxford… | `flyball.integrations.pymeasure` | the `measurement`/`control` properties you name as measurands, units read from their docstrings (`"in volts"` → V) with per-property overrides; any `control`/`setting` as an actuator |

```python
from qcodes.instrument_drivers.Keithley import Keithley2450
from flyball.integrations.qcodes import QCoDeSActuator, QCoDeSReader

smu = Keithley2450("smu", "TCPIP::192.168.1.5::INSTR")
rig.start_reader(QCoDeSReader("smu", smu, ["volt", "curr"]), period=1.0)
rig.attach_loop(..., QCoDeSActuator("bias", smu.source.voltage))
```

```python
from pymeasure.instruments.keithley import Keithley2400
from flyball.integrations.pymeasure import PyMeasureActuator, PyMeasureReader

smu = Keithley2400("GPIB::24")
rig.start_reader(PyMeasureReader("smu", smu, ["voltage", "current"]), period=1.0)
rig.attach_loop(..., PyMeasureActuator("bias", smu, "source_voltage"))
```

In a rig file the instrument goes under `links` — it is built once and
shared by every device that names it — and the devices carry the wrapper's
arguments:

```toml
[links.smu]
tag = "qcodes"
driver = "qcodes.instrument_drivers.Keithley.Keithley2450"
name = "smu"
args = ["TCPIP::192.168.1.5::INSTR"]

[links.k2400]
tag = "pymeasure"
driver = "pymeasure.instruments.keithley.Keithley2400"
adapter = "GPIB::24"

[[readers]]
period_s = 1.0
[readers.device]
tag = "qcodes_reader"
name = "smu"
link = "smu"
parameters = ["volt", "curr"]

[[actuators]]
tag = "pymeasure_actuator"
name = "bias"
link = "k2400"
attribute = "source_voltage"
```

`driver` is a dotted path to a class in a package you have installed;
nothing is fetched. What to know:

- The wrapped library owns the connection, so the link fakes are not
  involved; test against the library's own simulated instruments (QCoDeS
  `sims`, PyMeasure `FakeAdapter`), or point `driver` at a fake class of
  your own, as the test suite does.
- Their calls block, like any polled reader's; a slow instrument stretches
  a tick.
- A PyMeasure reader needs the property names — a driver exposes dozens, and
  reading them all would be slow and side-effecting. A QCoDeS reader takes
  every gettable numeric parameter by default.
- Neither wrapper imports its library, so `import flyball` needs nothing;
  the extra is for the drivers themselves.

When a wrapped driver is slow, blocks the wrong way, or lacks a function you
need, a bespoke driver is an `ScpiReader` with the query table filled in.

## Already there, not via a rig file

| device | where | link |
| --- | --- | --- |
| SHT4x humidity/temperature, alone or behind a TCA9548 mux | `examples/humidity` | I²C (`flyball.hardware.i2c`) |
| TB6612 / sysfs PWM pump pair | `examples/humidity` | Linux PWM |
| Bluesky `Readable`/`Movable` over any flyball device | `flyball.integrations.bluesky` | in-process |

## Not yet

- **EPICS** (pyepics / p4p) and **OPC UA** (asyncua): the `Reader.push`
  path is what they need; no adapter is written.
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
`descriptor` per stream (one per source, one per loop, with each key's
units), an `event` per sample or tick, and a `stop`.

```
flyball sessions
flyball export 12 --out run12.jsonl        # {"name": ..., "doc": ...} per line
GET /api/sessions/12/documents             # the same, as [[name, doc], ...]
```

In Python, `flyball.db.documents.documents(store, session_id)` yields the
`(name, doc)` pairs a Bluesky callback or `databroker.v2` consumes directly.
