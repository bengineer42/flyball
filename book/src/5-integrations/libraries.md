# Instrument libraries

**What.** Two Python instrument libraries already carry hundreds of drivers, written
declaratively enough that one wrapper each turns *every* driver into a
flyball device. Use these before writing a driver.

**Configure.** [`driver: qcodes`](../2-config/devices/drivers.md#qcodes) or
[`pymeasure`](../2-config/devices/drivers.md#pymeasure); every field is on
that page.

| library | drivers | driver | what comes through |
| --- | --- | --- | --- |
| **QCoDeS** (`flyball-qcodes[qcodes]`) | ~200: source-meters, lock-ins, cryogenic and quantum kit | `qcodes` (`flyball_qcodes`) | any gettable, numeric `Parameter` as a signal, with its unit read off the parameter unless overridden; a settable one is writable too |
| **PyMeasure** (`flyball-pymeasure[pymeasure]`) | ~150: Keithley, Agilent/Keysight, Lakeshore, Thorlabs, Anritsu, Oxford… | `pymeasure` (`flyball_pymeasure`) | the `measurement`/`control`/`setting` properties you name as signals, units read from their docstrings (`"in volts"` → V) with per-channel overrides |

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
- Neither wrapper imports its library at module load, so installing
  `flyball-qcodes`/`flyball-pymeasure` needs nothing extra; their own
  `qcodes`/`pymeasure` extra is for the drivers themselves.

When a wrapped driver is slow, blocks the wrong way, or lacks a function you
need, a bespoke driver is a `Scpi` subclass or a plain `Device` with the
query table filled in.

**Code.** `extensions/qcodes` (`flyball_qcodes`), `extensions/pymeasure`
(`flyball_pymeasure`); `pip install flyball-qcodes[qcodes]`,
`flyball-pymeasure[pymeasure]`.
