# What flyball does not do

Read this before a rig drives anything that can do harm. Each point is a
limit of the software, not a setting.

**Stop is a control function.** It is not a safety function and not an
emergency stop. Wire a hardware stop that removes power, interlocks and
thermal cut-outs independently of flyball.

**Stop sets only the outputs whose level it knows.** It sets each output
whose driver knows its inactive level to that level, and leaves every other
output where it is, energised if it was. `GET /api/rig/stop` (and the
runner's log at start) lists which is which
([Stopping the rig](../1-running/runner/access.md#stopping-the-rig)).
Whether an output's inactive level de-energises your load depends on your
wiring.

**Everything happens inside one process.** Everything flyball does to
outputs happens inside the runner. If it crashes, is killed, hangs or loses
power, outputs stay where they were until it starts again, and starting
again may write each driver's start-up value.

**"Stopped" is not "off".** "Stopped" means flyball refuses its own
automatic writes. It does not mean the outputs are off.

**Stopping at shutdown is best-effort** within the supervisor's stop window
(10 s under `flyballd`, which then kills the runner).

**A freeze has no bound flyball can promise.** `on_fault: freeze` holds an
output indefinitely. A time bound on it (`{freeze_s: …, then: …}`) is
checked inside flyball and does not act if flyball is stuck or dead
([`on_fault`](../2-config/controllers.md#on_fault-what-a-controller-does-about-a-faulty-source)).

**Nothing covers an output if flyball dies.** For protection that survives
flyball, use a device-side watchdog or a hardware cut-out. Every output is
"covered if flyball dies: no" (`covered_if_flyball_dies: false` in
`GET /api/rig/stop`).

**No run-on after a stop.** Keeping a fan running for a minute after a stop
is not supported; the only form is a fixed stop value, such as
`stop: {drive: 1}`, which leaves it running until someone changes it.
