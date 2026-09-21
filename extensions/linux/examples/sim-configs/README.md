# Simulated sensor configs

One overlay per sensor in `../sensors/`, on the humidity example's pattern
(`examples/humidity/sim.yaml`): the device keeps its real driver's signal
names, units and (where namespaced) addresses, but the driver itself is
swapped for the engine's generic `sim_daq`, bound to `sim_plant` links
(`engine/src/flyball/sim/plant.py`) instead of real hardware. A program or
dashboard built against the real config runs unchanged against the sim:

```
flyball-runner rig.yaml sim-configs/scd30.yaml
```

Every file nulls the real link (`i2c1`/`uart0`/`gpio0`) it no longer needs
and adds one or more `sim_plant` links, each a first-order `Lag` ("a heater
in a room, a stirred tank's temperature" -- settles toward a target with a
time constant) tuned to a plausible response time and range for that
quantity. `Fopdt` (lag plus dead time) is not used anywhere here: at this
level of fidelity none of these sensors has transport lag worth modelling
separately from its own settling time.

A `sim_plant`'s bare model has exactly one scalar output, so a chip with
several signals (SCD30's co2/temperature/humidity, BME280's
temperature/pressure) gets one small plant *per quantity*, not one shared
plant -- a shared plant would make every signal read the same number under
different units, not correlated values. Real correlation (temperature and
humidity moving together as a room breathes) would need a bespoke
multi-output plant class, like `examples/humidity`'s `sim_humidity_chamber`;
that's Python, so it's out of scope for this config-only pass and noted
per file where it would otherwise apply.

## Covered

Every driver that reads a sensor value: `sht31`, `htu21d`, `bme280`,
`bme680`, `ms5611`, `scd30`, `scd40` (SCD40/41), `sgp30`, `sgp40`, `ccs811`,
`mhz19`, `ezo_ph`, `hx711`, `tsl2591`, `veml7700`, `lps22hb`, `ph_probe`
(generic analog pH), `turbidity` (generic analog).

## Out of scope

Pure actuators, which don't have a "plausible reading over time" to fake:
`mcp4725` (DAC output), `dosing_pump`, `stepper`, `current_loop`,
`pulse_counter`, `solenoid_valve`.
