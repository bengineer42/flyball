# Devices

What is on the rig, keyed by name. Every entry is the same **envelope**
with the driver's own config flat beside it: every key that is not the
envelope's is the driver's.

```yaml
devices:
  dmm:
    driver: scpi
    label: Bench DMM
    poll_s: 0.5
    link: dmm
    channels: { voltage: { query: "MEAS:VOLT:DC?", unit: V } }
    signals:
      voltage: { range: [0, 30], precision: 3, warning: [0, 25] }
  wet_supply: { driver: sht4x, link: i2c1, address: 0x46, poll_s: 5 }
```

| key | type | |
| --- | --- | --- |
| `driver` | string | which driver builds it -- one of the [supported drivers](drivers.md), a board driver, or one from the runner's `drivers/` directory |
| `label` | string | shown instead of the name |
| `poll_s` | number | how often it is read; inherited down the tree, a signal's own winning. Unset: never polled (a pushed device) |
| `signals` | `{name: metadata}` | per-signal metadata, [below](#signals) |
| `inputs` | `{input: address}` | what this device follows on another, by the input's name: `{dry_humidity: hum_sensors.dry.humidity}` |
| `reads` | `{fail_after, backoff_s, give_up_after_s}` | when failed reads put it `offline`, and how it is retried, [below](#reads) |
| any other key | | the driver's own fields, listed per driver in [Supported drivers](drivers.md) and explained in [Where a device's options come from](generated.md); `link` names an entry under `links`, `pin: LABEL` resolves through the `board`. A nested `config:` is refused |

## `signals`

Metadata on the tree the driver declared -- what to show and what to
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
| `warning`, `alarm` | `[lo, hi]` | bands outside which the rig raises a condition on the signal, [below](#bands) |
| `on_no_value` | `fire` \| `ignore` | what a banded signal does while it has no value because of a fault: `fire` raises `band_unknown` after its grace, `ignore` raises nothing, [below](#a-banded-signal-with-no-value). Unset: `fire` with an `alarm` band, `ignore` with only `warning` |
| `limits` | `[lo, hi]` | narrows what a writable signal may be commanded to; it never widens the driver's. A demand is clamped to the intersection of the driver's limits and these, worked out at each demand; a signal the driver left unlimited takes these as they are. An end reaching past a driver end that is a number is refused at load (`limits (0, 5000) reach outside the driver's (0, 2500)`). A driver may declare an end that follows another of the device's signals (a supply's humidity, a max flow read from the device): it is intersected with that signal's value at each demand, and until that signal has a finite value (none yet, or NaN or infinite, counts as not known), a demand is refused (503, "limit not known yet") and a controller's write is held -- never passed unclamped, nor clamped to the other end. If the limits come out inverted at a demand (a dry supply read wetter than the wet one, or these clear of the driver's live band), the demand is refused (`LimitsInvertedError`, 422) and a controller's write is held. `null`: no narrowing -- the driver's limits stay |
| `max_rate` | `{per_second: N}` | how fast a demand may move; a faster one is clamped to the largest step the elapsed time allows, not refused. The elapsed time counts up to one update period -- the signal's `poll_s`, else the driving controller's `min_period_s`, else 1 s -- so a demand after a hold or a quiet spell moves one period's worth, not everything banked meanwhile. Unset: unlimited |
| `poll_s` | number | this signal's own rate; finite and above zero |
| `stale_after_s` | number (seconds) | finite and above zero; checked when a reading is delivered or the controller is regulated; a sensor that stops reporting is not caught. When it trips, a controller regulated from this signal is held: its law does not step and its demand is not applied, until a fresh reading. Unset: never checked |
| `tags` | `{key: value}` | added to the driver's: `{line: dry}` groups signals across devices in the UI |
| `access` | `"r"`, `"rp"`, … | keep only these of the flags the driver declared |
| `readable`, `published`, `writable` | `false` | drop one flag each; only `false` is accepted |

### Bands

The rig judges each reading of a banded signal as it is delivered, and
holds the result as a condition on the signal -- not the dashboard, whose
limits only colour a widget:

| the reading is | the signal holds | severity |
| --- | --- | --- |
| outside `alarm` | `band_alarm` | `error` |
| else outside `warning` | `band_warning` | `warning` |
| inside both | nothing | |

- **At most one.** Raising `band_alarm` clears `band_warning`; falling back
  from alarm to warning swaps them.
- **Raised at once**, on the first reading beyond the band.
- **Cleared with hysteresis**: only once readings have stayed back inside
  (or back down to the warning band) for `max(2 × poll_s, 1 s)` on the rig
  clock. A signal without `poll_s` (a pushed one) waits 1 s. A value hovering
  at the edge raises once and stays raised; it does not flap.
- **Details**: `{side: "low" | "high", value, bounds: [lo, hi]}`, the reading
  that raised it and the band it crossed.
- **Only numbers** are judged. A reading with no value (a NaN or an
  infinity is one: [no value](../../4-server/wire.md#a-reading-with-no-value))
  or a non-number leaves the condition as it was, and breaks the time back
  inside: it starts again at the next reading. A reading railed at one end
  (`at_limit`) past which a band edge lies is unknown to that band, and
  leaves it as it was too.
- **Removing** the band (`warning: null`, `alarm: null`) or the device
  clears the condition at once.

Each raise and clear is an event (`raised`, `cleared`) like any other
condition's, and the device's `conditions` carry it with `scope: signal`
and the signal's address as `subject`. `/api/health` counts the signals
holding each in `alarms` ([API](../../4-server/api.md)). A band alarm is not
a fault: it does not make the rig unhealthy.

```yaml
devices:
  thermocouple:
    signals:
      temperature: { warning: [30, 90], alarm: [10, 110], poll_s: 1.0 }   # clears after 2 s inside
```

#### A banded signal with no value

A band cannot say whether a reading with no value is inside it: the band
is unknown. What happens depends on why there is no value and on
`on_no_value`:

| the signal is | with `on_no_value: fire` | with `ignore` |
| --- | --- | --- |
| `invalid` (a NAMUR fault current, a failed sensor, a NaN) | `band_unknown` once it has lasted `max(2 × poll_s, 1 s)` | nothing |
| `not_applicable`, `pending` | nothing: benign | nothing |
| `stale` because its device is offline or its writes fail | nothing: the device's own `offline` / `write_failed` already counts | nothing |

`band_unknown` is `error` with an `alarm` band and `warning` with only a
`warning` band; its details are `{quality, reason, side}` (`side` when the
driver said which way it failed, as a NAMUR current does). It is counted in
`/api/health`'s `alarms.unknown`, never in `alarm`, and whatever band the
signal held before stays held meanwhile. It clears once 3 readings in a row
have a value; the band is judged again from the first of them. A single
bad reading therefore raises nothing: it would need to last the grace.

```yaml
devices:
  oxygen:
    signals:
      o2: { alarm: [18, 23] }                         # fire: a broken loop is an alarm
      trend: { warning: [0, 5], on_no_value: fire }   # a warning band that fires too
```

## Binding one device to another

`inputs` makes a device follow signals on another -- each an input the
driver declared, by its name, bound to an address at build. The humidity blender
follows the supply humidities its own sensors read:

```yaml
devices:
  blender:
    driver: dual_pump_blender
    inputs: { dry_humidity: hum_sensors.dry.humidity, wet_humidity: hum_sensors.wet.humidity }
```

What a driver may declare as an input, and how it reads one, is in
[Writing an actuator](../../3-extending/device/actuator.md).

## `reads`

```yaml
devices:
  hum_sensors:
    driver: sht4x_set
    poll_s: 2
    reads: { fail_after: 5, backoff_s: [2, 30], give_up_after_s: 3600 }
```

| key | type | |
| --- | --- | --- |
| `fail_after` | integer, at least 1 | reads that raise in a row before the device is `offline`. Unset: the runner's, else 3 |
| `backoff_s` | `[seconds, …]`, not empty | the waits between retries while offline, in turn; the last repeats. Unset: the runner's, else `[1, 2, 5, 15, 60]` |
| `give_up_after_s` | number, or `null` | stop retrying this long after the device went offline: polling stops, `offline` stays, and a `gave_up` event says so; a restart polls it again. `null` (the default): never give up |

Each key left out is the [runner's `reads:`](../runner.md#reads-when-a-failed-read-puts-a-device-offline),
then the default; that section describes the budget, the backoff and what
clears `offline`. The keys are per device, not per namespace or signal: the
runtime calls the driver's `read` once per period for the whole device, and
a raise ends that call whichever namespace it came from. A driver whose
namespaces fail on their own (one sensor of a set) can catch that one's
error and leave it out of the sample -- its last value stands until it goes
stale -- rather than raise, so the others keep being read.

## What a device gives you

Once built, a device is the same to everything above it, whichever driver
it has: its signals stream on `/ws/samples`, its tree and commands are
`GET /api/devices/{name}`, the UI draws a card per signal and per command,
the CLI grows a subcommand, and a controller may drive any writable signal
on it. Nothing about the driver leaks past the config.

