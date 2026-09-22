# Controllers

The **Controllers** page (`#/controllers`) is one card per writable signal: with a controller, the faceplate below; without, the signal's card alone -- adding a controller is done from [Config](rig.md), not here. What a controller is: [How a controller works](../../2-config/controllers.md#how-a-controller-works); how it is declared: [Controllers](../../2-config/controllers.md).

!!! tip "At the terminal"
    `flyball controllers` lists every faceplate's numbers; regulate / manual are routes for now -- [Controllers and tuning](../cli/controllers.md).

## Adding a controller

In [Config](rig.md), **Add controller** opens a four-step dialog: the
**Actuator** (the writable signal being commanded — the controller's
`target`), the **Sensor** (the published signal used to correct it — its
`source`), the law (a stored tuning, one configured here, or none) and the
feedforward, defaulted from the two units as the rig would. Signals already
driven or regulated by another controller are listed but disabled. Once an
actuator is chosen the sensor list leads with **Suggested** — the signals in
the actuator's own unit that nothing regulates yet — and puts everything
else under **All signals**; with no actuator chosen, or nothing that
matches, it is one list.

## The controller faceplate

`LoopPanel` (`ui/packages/react/src/panels/LoopPanel.tsx`) draws one
controller as three aligned rows, each with a bar:

- **Reading** — the process value, with a fill bar against the source
  signal's range, warn/alarm band ticks, and a notch at the target; a
  caption below it reading "N °C above/below target".
- **Target** — the setpoint, with the entry/Move control inline (one line,
  wrapping only below 900px) and a caption naming what it is following when
  it is a ramp.
- **Output** — the demand after limits (a controller's `expected ??
  demand`), with a fill bar against the target signal's `limits`. When the
  device cannot give the full demand (`value` differs from `requested` in
  its write state), the bar's fill turns to the alarm tint and the rail it
  is pinned against gets a 2px alarm end-cap — colour on the bar, not a text
  badge — and a **"requested …"** caption names what was asked for.

A banner above the rows reads "device offline", "signal at limit" or "open
loop" for the corresponding condition. Process and Drive mini trends (each
with a minimal axis pair: 3-4 y ticks at the signal's precision, sparse time
labels, no legend or toolbar) sit beside the rows; the Drive trend's y-range
is the target signal's `limits` when known, so "at limit" reads as the line
sitting on the rail. Below the trends, an always-open (no `<details>`) law &
feedforward summary gives the law's tag and every gain on one line, each
abbreviated field carrying its full name as a hover hint, and — on the
Controllers page only, never a dashboard widget — the device's own state and
commands, collapsed into the same card so a controller is one card, not two.
