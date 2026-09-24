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

- `instrument` must be one of the library's own instrument classes: a
  subclass of QCoDeS's `qcodes.instrument.Instrument` from under
  `qcodes.instrument_drivers.` (or `qcodes.instrument.`), or of PyMeasure's
  `pymeasure.instruments.Instrument` from under `pymeasure.instruments.`.
  The class is imported and called with the file's arguments, so any other
  path -- `instrument: os.system` with a shell command as `adapter` -- would
  run it; it is refused when the rig is validated instead, before anything
  is imported. Drivers of your own, or `qcodes_contrib_drivers`, are allowed
  by the machine, never the rig file: `FLYBALL_INSTRUMENT_PACKAGES` in the
  runner's environment, comma-separated package prefixes, and each class
  there must still subclass the library's `Instrument`
  ([`qcodes`](../2-config/devices/drivers.md#qcodes),
  [`pymeasure`](../2-config/devices/drivers.md#pymeasure)).
- The wrapped library owns the connection, so the link fakes are not
  involved; test against the library's own simulated instruments (QCoDeS
  `mock_instruments` and `sims`, PyMeasure `instruments.fakes`), or a fake
  subclass of its `Instrument` in a package named in
  `FLYBALL_INSTRUMENT_PACKAGES`, as the test suite does.
- Their calls block, like any polled device's; a slow instrument stretches
  a tick.
- A `publish: true` channel needs a getter -- a settable-only property is
  writable but not read back.
- Neither wrapper imports its library at module load, so installing
  `flyball-qcodes`/`flyball-pymeasure` needs nothing extra; their own
  `qcodes`/`pymeasure` extra is for the drivers themselves, and is needed
  wherever a rig that names one is validated.

When a wrapped driver is slow, blocks the wrong way, or lacks a function you
need, a bespoke driver is a `Scpi` subclass or a plain `Device` with the
query table filled in.

**Code.** `extensions/qcodes` (`flyball_qcodes`), `extensions/pymeasure`
(`flyball_pymeasure`); `pip install flyball-qcodes[qcodes]`,
`flyball-pymeasure[pymeasure]`.
