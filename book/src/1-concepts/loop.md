# The loop

A **loop** is the unit of control: one channel, one law, one actuator, one
reference. It is named after the actuator it drives.

## One tick

Every time a reading arrives on the loop's channel, the loop ticks:

1. **Resolve the setpoint** for this instant. The reference is either a fixed
   value or a *generator* — a function of time, such as a ramp — evaluated
   exactly at the reading's timestamp.
2. **Step the law.** The law takes `(elapsed, reading, setpoint)` and returns a
   **correction**: the offset to add to the setpoint.
3. **Form the demand**: `demand = setpoint + correction`.
4. **Hand it to the actuator**: `expected = actuator.set_demand(demand)`.
5. **Remember** the reading, the demand and what the actuator expects to
   deliver, so the next tick's anti-windup and any observer can see them.

Nothing here knows what the quantity is. The loop works in the channel's unit
throughout; converting a temperature demand into a heater power is the
actuator's business.

## Why a correction, not an output

The law produces an *offset from the setpoint*, and the demand is in the
same units as the reading. Two things follow:

- **Open loop is a law that returns zero.** Set the law to `OpenLoop` and the
  loop drives the actuator at the setpoint itself. That is how an experiment
  or a manual hold is run through the same path as regulation.
- **Handover is arithmetic.** Switching from manual to regulating, or swapping
  a tuning, means choosing what the correction should be at the instant of
  the switch. See [Handover](../4-control/loop.md#handover).

## Modes

| mode | what ticks do |
| --- | --- |
| `manual` | nothing; the actuator is driven by commands |
| `open` | apply the setpoint with the correction held |
| `regulating` | step the law and apply |

Mode says what the loop is *doing*. Who is *allowed* to change it is a
separate question, answered separately.

## Time

A law never owns a clock. It is given `elapsed` — seconds since it was last
reset or resumed — and derives its own interval from the previous call. A
generator, by contrast, is a function of *absolute* time, because a ramp is
anchored to the instant it must land. That split is deliberate and is why the
two take different time arguments.

Next: [Programs](programs.md).
