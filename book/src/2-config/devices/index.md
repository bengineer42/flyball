# Devices

What is on the rig, keyed by name. Every entry is the same **envelope**
around the driver's own config. The driver's fields may sit flat beside the
envelope or under `config:`; both mean the same.

```yaml
devices:
  dmm:                                   # layered
    driver: scpi
    label: Bench DMM
    poll_s: 0.5
    config:
      link: dmm
      channels: { voltage: { query: "MEAS:VOLT:DC?", unit: V } }
    signals:
      voltage: { range: [0, 30], precision: 3, warning: [0, 25] }
  wet_supply: { driver: sht4x, link: i2c1, address: 0x46, poll_s: 5 }   # flat
```

| key | type | |
| --- | --- | --- |
| `driver` | tag | which driver builds it -- one of the [supported drivers](drivers.md), a board driver, or one from the runner's `drivers/` directory |
| `label` | string | shown instead of the name |
| `poll_s` | number | how often it is read; inherited down the tree, a signal's own winning. Unset: never polled (a pushed device) |
| `config` | object | the driver's own fields, listed per driver in [Supported drivers](drivers.md) and explained in [Where a device's options come from](generated.md); `link` names an entry under `links`, `pin: LABEL` resolves through the `board` |
| `signals` | `{name: override}` | per-signal metadata, [below](#signals) |
| `bound` | `{role: address}` | inputs this device follows on another: `{dry_humidity: hum_sensors.dry.humidity}` |

## `signals`

Overrides on the tree the driver declared -- what to show and what to
guard, never new access. A key that is a namespace takes `label`,
`poll_s`, `tags` and its own `signals:`; a key that is a signal takes the
keys below. A key left out keeps the driver's value; a key set to `null`
clears it (`label: null` shows the titlecased name, `warning: null` drops the
band, `poll_s: null` inherits from the namespace again) -- except `limits:
null`, which drops only the file's narrowing, never the driver's limits.

| key | type | |
| --- | --- | --- |
| `label` | string | |
| `range` | `[lo, hi]` | the axis and gauge extent |
| `precision` | int | decimal places shown |
| `warning`, `alarm` | `[lo, hi]` | bands outside which a condition is raised |
| `limits` | `[lo, hi]` | narrows what a writable signal may be commanded to; it never widens the driver's. A demand is clamped to the intersection of the driver's limits and these, worked out at each demand; a signal the driver left unlimited takes these as they are. An end reaching past a driver end that is a number is refused at load (`limits (0, 5000) reach outside the driver's (0, 2500)`). A driver may declare an end that follows another of the device's signals (a supply's humidity, a max flow read from the device): it is intersected with that signal's value at each demand, and until that signal has a finite value (none yet, or NaN or infinite, counts as not known), a demand is refused (503, "limit not known yet") and a controller's write is held -- never passed unclamped, nor clamped to the other end. If the limits come out inverted at a demand (a dry supply read wetter than the wet one, or these clear of the driver's live band), the demand is refused (`LimitsInvertedError`, 422) and a controller's write is held. `null`: no narrowing -- the driver's limits stay |
| `max_rate` | `{per_second: N}` | how fast a demand may move; a faster one is clamped to the largest step the elapsed time allows, not refused. The elapsed time counts up to one update period -- the signal's `poll_s`, else the driving controller's `min_period_s`, else 1 s -- so a demand after a hold or a quiet spell moves one period's worth, not everything banked meanwhile. Unset: unlimited |
| `poll_s` | number | this signal's own rate; finite and above zero |
| `stale_after_s` | number (seconds) | finite and above zero; checked when a reading is delivered or the controller is regulated; a sensor that stops reporting is not caught. When it trips, a controller regulated from this signal is held: its law does not step and its demand is not applied, until a fresh reading. Unset: never checked |
| `tags` | `{key: value}` | added to the driver's: `{line: dry}` groups signals across devices in the UI |
| `access` | `"r"`, `"rp"`, … | keep only these of the flags the driver declared |
| `readable`, `published`, `writable` | `false` | drop one flag each; only `false` is accepted |

## Binding one device to another

`bound` makes a device follow signals on another -- an input the driver
declared by role, resolved to an address at build. The humidity blender
follows the supply humidities its own sensors read:

```yaml
devices:
  blender:
    driver: dual_pump_blender
    bound: { dry_humidity: hum_sensors.dry.humidity, wet_humidity: hum_sensors.wet.humidity }
```

What a driver may declare as an input, and how it reads one, is in
[Writing an actuator](../../3-extending/device/actuator.md).

## What a device gives you

Once built, a device is the same to everything above it, whichever driver
it has: its signals stream on `/ws/samples`, its tree and commands are
`GET /api/devices/{name}`, the UI draws a card per signal and per command,
the CLI grows a subcommand, and a controller may drive any writable signal
on it. Nothing about the driver leaks past the config.

