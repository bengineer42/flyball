# Not yet

What people ask for that is not written, and which extension point each
would use.

- **EPICS** (pyepics / p4p) and **OPC UA** (asyncua): a pushed device --
  a subscription that calls `self.push` as values arrive
  ([Writing a sensor](../3-extending/device/sensor.md#a-pushed-device)); no adapter
  is written.
- **NI-DAQmx / LabJack** analogue I/O. (Bare Linux buses — I²C, SPI, GPIO,
  PWM, 1-Wire — are `flyball-linux`: [Raspberry Pi and Linux buses](linux.md).)
- Vendor packages with the query tables filled in (`flyball-keithley`,
  `flyball-alicat`). The entry-point discovery they would use exists; the
  packages do not. Until then the wrapped libraries above cover most bench
  kit.
- An instrument's own error queue (`SYST:ERR?`) surfacing as a condition.

The pushed-device path itself is built and used by [the humidity rig](https://bengineer42.github.io/humctrl/3-devices/blender/)'s
blender readbacks; only the adapters are missing.
