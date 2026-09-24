# Devices

What is on the rig, keyed by name -- a key: lower-case letters, digits and
`_`, starting with a letter, `-` read as `_` ([Names](../../7-reference/rig-file.md#names)).
Every entry is the same **envelope**
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
  wet_supply: { driver: sht4x, link: i2c1, i2c_address: 0x46, poll_s: 5 }
```

| key | type | |
| --- | --- | --- |
| `driver` | string | which driver builds it -- one of the [supported drivers](drivers.md), a board driver, or one from the runner's `drivers/` directory |
| `label` | string | shown instead of the name |
| `poll_s` | number | how often it is read; inherited down the tree, a signal's own winning. Unset: never polled (a pushed device) |
| `signals` | `{name: metadata}` | per-signal metadata, [below](#signals) |
| `inputs` | `{input: address or number}` | what each of this device's inputs follows: another device's signal, or a number. Every input the driver declares must be given one -- an input has no default, [below](#binding-one-device-to-another) |
| `reads` | `{fail_after, backoff_s, give_up_after_s}` | when failed reads put it `offline`, and how it is retried, [below](#reads) |
| `retry_max_age_s` | number (seconds) | how long a value a failed write kept may wait to be sent again; older is dropped, not sent, [below](#a-write-that-fails). Finite and above zero; unset: 60 s |
| `stop` | `{demand path: number or keep}` | what a stop writes to each writable demand, overriding the driver's `off`; `keep` leaves it as it is. Refused on a device whose driver stops it with a command, [below](#stop-what-a-stop-writes) |
| `on_shutdown` | `stop` \| `keep` | what the runner's shutdown does to this device. Unset: the [runner's `on_shutdown`](../runner.md), [below](#on_shutdown-what-shutting-down-does) |
| `permissive` | `{demand path: {signal, above, below}}` | a write to the demand is refused unless `signal`'s value is inside the band, [below](#permissive-a-write-only-while-another-signal-allows-it) |
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
| `stale_after_s` | number (seconds) | how long the signal may go without a reading before the rig marks it `stale`, [below](#liveness-a-signal-that-stops-arriving). Finite and above zero. Unset: `max(3 × poll_s, 5 s)` from its own `poll_s` while its device is polled; a pushed signal is then not judged at all. A controller also holds (`stale_input`) on a reading that arrives already older than this |
| `tags` | `{key: value}` | added to the driver's: `{line: dry}` groups signals across devices in the UI |
| `record` | `false` | left out of a recording started with the default selection (`--record`, `recording: true`, or `POST /api/recording` with no signals): a raw value only the rig needs. Unset: recorded |
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
condition's, and the device's `conditions` carry it with `subject_kind: signal`
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
| `invalid` (a NAMUR fault current, a failed sensor, a NaN) | `band_unknown` once it has accrued `max(2 × poll_s, 1 s)` of fault time, judged on the rig clock: a signal that goes quiet after one `invalid` still raises it | nothing |
| `stale` by age (`silent`, `last_read`, `never_read`) | `band_unknown` at once: the threshold was its grace | nothing |
| `not_applicable`, `pending` | nothing: benign | nothing |
| `stale` because its device is offline or hung, or its writes fail | nothing: the device's own `offline` / `hung` / `write_failed` already counts | nothing |

`band_unknown` is `error` with an `alarm` band and `warning` with only a
`warning` band; its details are `{quality, reason, side}` (`side` when the
driver said which way it failed, as a NAMUR current does). It is counted in
`/api/health`'s `alarms.unknown`, never in `alarm`, and whatever band the
signal held before stays held meanwhile. It clears once 3 readings in a row
have a value; the band is judged again from the first of them. Fault time
accrues only while the signal has no value: a reading with a value pauses
it without resetting it. A single bad reading therefore raises nothing,
while one that keeps flickering does.

```yaml
devices:
  oxygen:
    signals:
      o2: { alarm: [18, 23] }                         # fire: a broken loop is an alarm
      trend: { warning: [0, 5], on_no_value: fire }   # a warning band that fires too
```

### Liveness: a signal that stops arriving

The rig judges, on its own clock, whether each measurement is still
arriving -- a readout, or a demand read back from the hardware
(`readback: sensed`), that publishes. Its threshold is its `stale_after_s`,
else `max(3 × poll_s, 5 s)` from its own `poll_s` while its device is
polled. When nothing has arrived for that long, the rig pushes a reading
with no value on it, `stale`, stamped at that instant: a chart breaks at the
threshold, a controller regulating on it freezes, a limit that follows it
fails closed, and its band is unknown. The reason says which:

| reason | what happened |
| --- | --- |
| `silent` | it had readings, and its device has delivered nothing within the threshold either |
| `last_read` | its device still delivers other signals, but not this one (a driver leaving out one failed sensor of a set) |
| `never_read` | nothing has arrived since its device's first successful read (or since polling began, if there has been none): `pending` past its deadline |
| `device_offline`, `device_hung` | its device holds `offline` or `hung`: its read path went `stale` at once, not after the threshold |

The next reading ends it. Not judged: settings, configs, a demand whose
reading is the committed value (`readback: echo`: its liveness is its
writes', `stale(write_failed)`), a record such as `last.<command>`, a signal
whose newest reading is `not_applicable` (undefined, not late), and a
pushed signal with no `stale_after_s`. The threshold in force is
`stale_after_s` on the signal in `GET /api/devices/{name}` (null: not
judged).

A poll's read still in flight `max(3 × poll_s, 5 s)` after it began is stuck
in its driver: the device holds `hung` (`error`), and what its reads
delivered is `stale(device_hung)` at once. The read returning, however it
returns, clears `hung`.

## Binding one device to another

`inputs` gives each of a device's inputs what it follows: another device's
signal, by its address, or a number. The humidity blender follows the
supply humidities its own sensors read, or takes them as numbers on a rig
with no line sensors:

```yaml
devices:
  blender:
    driver: dual_pump_blender
    inputs: { dry: hum_sensors.dry.humidity, wet: hum_sensors.wet.humidity }
# or, with no sensor on the lines:
    inputs: { dry: 36.5, wet: 88.5 }
```

- **No default.** Every input the driver declares must be given an address
  or a number; one left out, or a name the driver does not declare, is
  refused when the file loads, and `flyball rig check` says which
  (`device 'blender': input 'dry' is neither bound nor a number`).
- **A number** has its value from the start, and is always `ok`.
- **An address** is resolved once, at build: to a signal that publishes, or
  to a namespace with something published under it. Until the signal's
  first reading the input is `pending`, and while its reading has no value
  (`invalid`, `stale`) the input has none either -- nothing stands in for
  it. A limit that follows the input is then not known, so a demand is
  refused naming it (`its limit follows 'dry' (pending)`), and a controller
  driving through it holds `limit_unknown` -- `info` while the input is only
  `pending`, `warning` once it is `stale` or `invalid`.
- **An output computed from an input carries its quality.** A value
  computed from a `stale` input is `stale` with the same reason, not a
  number from an old one.
- **No cycles.** A device that follows itself through `inputs:` --
  directly, or through other devices -- is refused at load, the path
  named: `a cycle through inputs: a.inputs.x <- b.out; b.inputs.x <- a.out`.

An operator-entered number that may change while the rig runs is a
[`values`](drivers.md#values) device's signal, bound like any other:
`inputs: { dry: bench.dry_supply }`.

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

## A write that fails

A commit that raises -- on the delivery path, or on a blocking device's
writer thread -- holds `write_failed` on the device until a commit
succeeds, and every `readback: echo` demand on it reads
`stale(write_failed)` meanwhile. The values it carried are **kept**, not
dropped: they go out with the next commit of the device (a newer demand on
the same signal replaces its own), and the rig retries on its clock without
waiting for other traffic -- first after `min(poll_s, 5 s)` (5 s for a device
with no `poll_s`), then each after twice the last, up to 60 s. A commit
that sets a kept value raises a `resent` event (`re-sent heater.power=40,
staged at 12.500 s`). A kept value older than `retry_max_age_s` is dropped
with a `write_dropped` event, not sent: that demand stays
`stale(write_failed)` until a new demand of it commits. A manual demand
whose commit fails still answers with the error; its value is kept and
retried all the same.

```yaml
devices:
  pumps: { driver: pwm_pair, link: pwm0, retry_max_age_s: 20 }   # a stale flow is worse than none
```

Retrying sends the latest value of a demand again. That suits a level (a
power, a flow); a demand that is not idempotent (a dose) should not rely on
it. A stop, or a latch on the device, replaces what was kept: the kept
values are dropped and their retry cancelled.

## `stop`: what a stop writes

Every device has a **resolved stop**: what a
[software stop](../../1-running/runner/access.md#stopping-the-rig), a
shutdown or a controller's [`on_fault: stop`](../controllers.md#on_fault-what-a-controller-does-about-a-faulty-source)
does to it.

- **A stop command.** A device whose driver has one (the humidity blender's
  `stop`, `dosing_pump` and `stepper`'s `stop`, `mcp4725`'s `power_down`,
  `scpi` with a `stop_command:`) is stopped by running it. `stop:` values
  are refused at load on such a device.
- **Otherwise, per writable demand,** the first that applies: the rig
  file's `stop:` value (a number, or `keep`); else the driver's declared
  **`off`**, the output's inactive level (a PWM duty's 0 %); else `keep`
  -- left as it is, energised if it was.

A driver declares `off` only where it cannot be wrong: never on an inverted
output, never on a span that straddles 0. Each driver's is in its section
of [Supported drivers](drivers.md). A declared `off` is written even
outside `limits`: limits bound regulation, not switching the output off. A
`stop:` number is checked against the signal's limits at load.

```yaml
devices:
  heaters:
    driver: pwm_channel
    link: pwm0
    channel: 0
    stop: { drive: keep }       # left as it is on a stop
  fan:
    driver: pwm_channel
    link: pwm0
    channel: 1
    stop: { drive: 1 }          # a fan that must keep running after a stop
  direction:
    driver: gpio_line
    link: gpio0
    line: 17
    direction: output
    stop: { on: keep }          # a direction or select line: 0 is not "off"
```

The driver cannot tell a heater from a fan. A fan or a coolant pump that
must keep running after a stop needs `stop: {drive: 1}` or `keep`. Run-on
(the fan for a minute, then off) is not supported:
[What flyball does not do](../../0-overview/limits.md).

`GET /api/rig/stop` lists every writable output with what a stop would do
to it and why (its `origin`: `off`, `you_said`, `nobody_said`, `command`), and warns about
a controller's output nobody gave a stop, which a stop leaves energised and
open loop. The runner logs the same warnings at start. `flyball rig check`
checks the file's schema only and cannot see a driver's `off`, so this
list needs a running rig.

## `on_shutdown`: what shutting down does

When the runner shuts down or restarts, it applies each device's resolved
stop, best-effort within the supervisor's stop window. `on_shutdown: keep`
on a device leaves that device as it is; the runner's own
[`on_shutdown: keep`](../runner.md) (or `--on-shutdown keep`) leaves every
device as it is. `keep` means flyball writes nothing on the way out, so the
outputs stay energised with no process watching them. The next start
builds each driver again, which writes its build value (0 or `initial`
for `pwm_channel`, `gpio_line`, `mcp4725`, `stepper`).

```yaml
devices:
  circulator: { driver: gpio_line, link: gpio0, line: 22, direction: output, on_shutdown: keep }
```

## `permissive`: a write only while another signal allows it

```yaml
devices:
  heaters:
    driver: pwm_channel
    link: pwm0
    channel: 0
    permissive:
      drive: { signal: chamber.flow, above: 0.5 }   # heat only with air flowing
```

A write to `drive` is refused (409) unless `chamber.flow` reads above 0.5.
`above` and `below` are strict bounds; give either or both. It fails
closed: a signal with no reading yet, or none with a value (`stale`,
`invalid`, `pending`), refuses the write. A write of the demand's resolved
stop value is always permitted, and a stop ignores the permissive. A
controller driving the demand is held (frozen, condition `not_permitted`)
rather than refused, and resumes when the permissive allows it. Only
`{signal, above, below}` is built; richer conditions are not.

## What a device gives you

Once built, a device is the same to everything above it, whichever driver
it has: its signals stream on `/ws/samples`, its tree and commands are
`GET /api/devices/{name}`, the UI draws a card per signal and per command,
the CLI grows a subcommand, and a controller may drive any writable signal
on it. Nothing about the driver leaks past the config.

