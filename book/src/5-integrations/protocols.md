# Instrument protocols

**What.** Text instruments that answer SCPI or any line-per-command dialect,
over VISA (GPIB, USB-TMC, LAN, serial) or a raw serial port; register
instruments -- PID controllers, MFCs, chillers, PLCs -- over Modbus TCP or
RTU.

**What comes through.** Each query, write template or register you name
becomes a signal with a unit: `query` alone is `[RP]`, `write` alone `[W]`,
both `[RPW]`; a `holding` register with `write: true` is `[RPW]`. Replies are
parsed as numbers; `scale` converts. The `scpi` driver adds `write` and
`query` commands for bring-up. An instrument that stops answering marks its
device `offline` until the next good read.

**Configure.** A link per connection under `links:`
([`visa`, `serial`, `modbus_tcp`, `modbus_rtu`](../2-config/links.md#text-instruments)),
a device per instrument with [`driver: scpi`](../2-config/devices/drivers.md#scpi) or
[`modbus`](../2-config/devices/drivers.md#modbus). Every real link has a
scripted fake (`fake_text`, `fake_registers`), so the same file runs with
nothing plugged in. Each real link holds one lock, so two devices sharing an
instrument never interleave a query with a write.

**Code.** `flyball.hardware.links` (the `TextLink`/`RegisterLink` protocols
only); `extensions/visa` (`flyball_visa`: the text links and `scpi`) and
`extensions/modbus` (`flyball_modbus`: the register links and `modbus`),
each its own installable package, like `extensions/qcodes` and
`extensions/pymeasure`. `pip install flyball-visa[visa]` (pyvisa +
pyvisa-py, no NI runtime), `flyball-visa[serial]`, `flyball-modbus[modbus]`.

**Beyond the table.** A reply that is not a number, a command sequence, an
instrument's own error queue: subclass `Scpi` or write a `Device` --
[Writing a sensor](../3-extending/device/sensor.md#talking-to-an-instrument).
