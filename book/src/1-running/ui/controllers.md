# Controllers

The **Controllers** page (`#/controllers`) is one card per writable signal: with a controller, the faceplate below; without, the signal's card alone, including a device's other demands when one of them is driven (a furnace's other zones) -- adding a controller is done from [Config](rig.md), not here. What a controller is: [How a controller works](../../2-config/controllers.md#how-a-controller-works); how it is declared: [Controllers](../../2-config/controllers.md).

!!! tip "At the terminal"
    `flyball controllers` lists every faceplate's numbers; regulate / manual are routes for now -- [Controllers and tuning](../cli/controllers.md).

## Adding a controller

In [Options › Rig file](rig.md), **Add controller** opens a five-step dialog: the
**Output** (the demand the controller drives — its `output`), the
**Measured** signal (the published signal it regulates — its `measured`),
the law (a stored tuning, one configured here, or none), the
feedforward, defaulted from the two units as the rig would, and **On fault**:
what it does once its measured signal has had no value for its wait
([`on_fault`](../../2-config/controllers.md#on_fault-what-a-controller-does-about-a-faulty-source):
freeze, the default; manual; stop; stop device), with an optional "freeze
first for" time that makes it `{freeze_s, then}`, and the optional
**setpoint period** (`setpoint_period_s`: how often a moving setpoint's
feedforward is re-applied between readings). A box left blank takes the
rig's default and is not sent. Signals already
driven or regulated by another controller are listed but disabled. Once an
output is chosen the measured list leads with **Suggested** — the signals in
the output's own unit that nothing regulates yet — and puts everything
else under **All signals**; with no output chosen, or nothing that
matches, it is one list.

## The controller faceplate

When something stops the controller from regulating, one amber line under
its header says what: the software stop, its own fault action, or a
permissive holding its output (`not_permitted`). A fault-action latch has a
**Reset** there, behind a confirmation; the software stop is reset from the
banner at the top of the page.

`LoopPanel` (`ui/packages/react/src/panels/LoopPanel.tsx`) draws one
controller as three aligned rows, each with a bar:

- **Measured** — the measured value (PV), with a fill bar against the
  measured signal's range, warning/alarm band ticks, and a notch at the
  setpoint; a caption below it reading "N °C above/below setpoint". With no
  value it reads `—` and why ("invalid: open circuit", "stale: device silent"),
  the last usable value on hover, never the number before it.
- **Setpoint** — the setpoint (SP), with the entry/Move control inline (one line,
  wrapping only below 900px) and a caption naming what it is following when
  it is a ramp.
- **Output** — the output (OP) after limits (a controller's `expected ??
  output`), with a fill bar against the output signal's `limits`. When the
  device cannot give the full output (`value` differs from `requested` in
  its write state), the bar's fill turns to the alarm tint and the rail it
  is pinned against gets a 2px alarm end-cap — colour on the bar, not a text
  badge — and a **"requested …"** caption names what was asked for.

The header carries **Manual** (put the controller in manual: it stops
regulating, the output keeps its last demand and takes demands directly;
this is not the rig's software stop and writes nothing) and the remove
button; removing a regulating controller puts it in manual first.

A banner above the rows reads "measured offline", "frozen", "no recent
reading", "output at limit" or "open loop" for the corresponding condition.
"Frozen" is the rig's `frozen` condition on the controller: its measured
signal has no value, so the law is not stepped and nothing is written until it
reads again; it is information (ⓘ) for a benign `n/a`, a warning (⚠) for a
fault. "No recent reading" is the rig's own `stale` reading on the measured
signal. A re-apply between readings (a moving setpoint's feedforward) is joined
across on the Process trend, not drawn as a gap. Process and Drive mini trends (each
with a minimal axis pair: 3-4 y ticks at the signal's precision, sparse time
labels, no legend or toolbar) sit beside the rows; the Drive trend's y-range
is the output signal's `limits` when known, so "at limit" reads as the line
sitting on the rail. Below the trends, an always-open (no `<details>`) law &
feedforward summary gives the law's type and every gain on one line, each
abbreviated field carrying its full name as a hover hint, and — on the
Controllers page only, never a dashboard widget — the device's own state and
commands, collapsed into the same card so a controller is one card, not two.
