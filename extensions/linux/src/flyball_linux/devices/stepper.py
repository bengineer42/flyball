"""A step/direction stepper motor as a device: `move(steps)` over two (or three) GPIO lines.

**Never run against real hardware.** This driver has only been exercised against the fake
link in `linux/tests`; the timing, the calibration model and the "always leave the driver
safe" guarantee below are unverified on an actual stepper.

Step/direction is the interface virtually every stepper driver IC exposes -- A4988, DRV8825,
the TMC-series in legacy mode -- so this covers the vast majority of real stepper hardware
without needing a per-chip driver: `step` is a pulse train, one rising edge per motor step,
and `direction` is a level the driver IC latches before each step. That is deliberately the
level this module works at. The alternative -- bit-banging the coil phase sequence directly
from Python -- is exactly the kind of fragile, unbounded-latency timing this project already
flagged as a real risk in `flyball_chips.hx711` (a CPython GPIO call crosses
into the kernel with no bound on how long it takes; the HX711 driver's docstring is explicit
about trusting two syscalls back to back rather than hitting a microsecond deadline). A
stepper's phase sequencing has *tighter* timing requirements than HX711's clock line, not
looser, and every driver IC on the market exists precisely to take that job away from the
host -- so this driver targets step/direction and leaves phase-level bit-banging out of
scope entirely, on the same grounds `dosing_pump.py` gives for staying on top of existing
primitives rather than inventing new ones.

An optional `enable` line (common on stepper driver ICs, active-low: driving it low enables
the motor coils) is switched on for the duration of a move and off afterwards, so the motor
does not sit energised -- and drawing current -- between moves. It is skipped when not
configured; a driver IC wired enable-always-low needs no line here.

`move(steps)` is a long command, like `dosing_pump.dispense`: it clocks out `abs(steps)`
pulses at `steps_per_s`, blocking its caller, off the rig lock. The gaps between pulses are
waited on `Device.wait`, in the rig's time, so `stop` (which calls `Device.cancel`) ends the
move after the pulse in progress. It mirrors that module's `finally`-based safety guarantee
-- whatever happens mid-move (a stop, a hardware error from the underlying link), the enable
line is always switched back off and the direction line is left at whatever it was last
driven to, never half-toggled. The pulse width itself is real time: it is the driver IC's.

`steps_per_unit` is an optional calibration constant (steps per degree, steps per mm of
linear travel) so `move()` can be called in the rig's own engineering unit instead of raw
steps; a plain `move(steps=200)` still works with no calibration configured.

`position` -- the raw step count the driver has tracked since it started -- is deliberately
declared `[R]`, not `[RP]`: it is an internal detail of the move command (like a stepper's own
phase state), not a quantity a rig author normally wants trended on a dashboard or written to
the recorder by default. It stays readable on demand for debugging, but is not published on
schedule. A rig's `signals:` metadata can only *narrow* a driver's declared access (see
`flyball.foundation.device._set_signal_meta`, which only clears flags via `Signal.restrict` and
never sets them) -- so turning `position` into a recorded `[RP]` signal is not something a
rig file can do; it needs a driver code change.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from flyball.foundation.config import resolve
from flyball.foundation.device import (
    Access,
    Committable,
    DriverConfig,
    Node,
    Readable,
    Role,
    Sample,
    Setting,
    SignalSpec,
    command,
)
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Hertz, One
from pydantic import Field

from flyball_linux.links.gpio import GpioLink, GpioLinkConfig

POSITION = Quantity("position", One)
RATE = Quantity("rate", Hertz)


class Stepper(Readable, Committable):
    """`move(steps)`: clock `abs(steps)` step pulses at `steps_per_s`, direction set first.

    `step_line` and `direction_line` are required; `enable_line` is optional and, when given,
    is driven active (low, i.e. `on()`-equivalent electrical low through the usual GPIO
    convention here) for the duration of the move and released afterwards -- see the module
    docstring for why. `steps_per_unit`, if set, lets `move()` take engineering units.
    """

    steps_per_s = Setting("steps_per_s", "Step pulse rate", RATE)

    def __init__(
        self,
        name: str,
        link: GpioLink,
        step_line: int,
        direction_line: int,
        steps_per_s: float,
        enable_line: int | None = None,
        enable_active_low: bool = True,
        steps_per_unit: float | None = None,
        pulse_width_s: float = 0.0005,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        if steps_per_s <= 0:
            raise ValueError(f"{name}: steps_per_s must be positive")
        if steps_per_unit is not None and steps_per_unit <= 0:
            raise ValueError(f"{name}: steps_per_unit must be positive")
        if pulse_width_s < 0:
            raise ValueError(f"{name}: pulse_width_s must not be negative")
        self.link = link
        self.step_line = step_line
        self.direction_line = direction_line
        self.enable_line = enable_line
        self.enable_active_low = enable_active_low
        self.steps_per_unit = steps_per_unit
        self.pulse_width_s = pulse_width_s
        self._direction = True
        self._position = 0

        link.claim_output(step_line, False)
        link.claim_output(direction_line, self._direction)
        if enable_line is not None:
            link.claim_output(enable_line, enable_active_low)  # disabled (inactive) at startup

        self.bind((
            SignalSpec(
                name="position",
                quantity=POSITION,
                access=Access.R,
                role=Role.READOUT,
                precision=0,
                initial=0.0,
            ),
        ))
        self.steps_per_s.push(steps_per_s)

    @property
    def config(self) -> StepperConfig:
        return StepperConfig(
            link="",
            step_line=self.step_line,
            direction_line=self.direction_line,
            steps_per_s=self.steps_per_s.value,
            enable_line=self.enable_line,
            enable_active_low=self.enable_active_low,
            steps_per_unit=self.steps_per_unit,
            pulse_width_s=self.pulse_width_s,
        )

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """`position`, readable on demand -- see the module docstring for why it is `[R]`."""
        yield Sample(self.root, time_ns, {self.signals["position"]: float(self._position)})

    def _enable(self, on: bool) -> None:
        if self.enable_line is None:
            return
        self.link.set(self.enable_line, on != self.enable_active_low)

    def _set_direction(self, forward: bool) -> None:
        self._direction = forward
        self.link.set(self.direction_line, forward)

    def _pulse(self) -> None:
        self.link.set(self.step_line, True)
        if self.pulse_width_s > 0:
            time.sleep(self.pulse_width_s)
        self.link.set(self.step_line, False)

    @command(long=True)
    def move(self, steps: float) -> None:
        """Move `steps` steps (negative reverses direction), or units if `steps_per_unit` is set.

        Direction is set once, then `abs(count)` pulses are clocked at `steps_per_s`. A
        `stop` ends the move after the pulse in progress; `position` counts the pulses
        sent. The enable line (if configured) is always switched back off afterwards, and
        direction is always left at whatever it was last set to -- even if a pulse mid-move
        raises.
        """
        count = round(steps if self.steps_per_unit is None else steps * self.steps_per_unit)
        if count == 0:
            return
        forward = count > 0
        n = abs(count)
        interval_s = 1.0 / self.steps_per_s.value
        try:
            self._enable(True)
            self._set_direction(forward)
            for i in range(n):
                self._pulse()
                self._position += 1 if forward else -1
                if i < n - 1 and self.wait(max(0.0, interval_s - self.pulse_width_s)):
                    break  # stopped
        finally:
            self._enable(False)

    @command(stops=True)
    def stop(self) -> None:
        """End a move in progress and release the enable line; direction and position stay.

        The device's stop: a rig stop runs it. Without an `enable_line` it only ends the
        move -- the coils stay as they were.
        """
        self.cancel()
        self._enable(False)


class StepperConfig(DriverConfig[Stepper], type="stepper"):
    """`driver: stepper`: `{ link, step_line, direction_line, steps_per_s }`.

    `enable_line` (optional) is driven active for the duration of a move and released
    afterwards; `enable_active_low` (default true) matches the common driver-IC convention.
    `steps_per_unit` (optional) lets `move()` be called in engineering units instead of raw
    steps. `pulse_width_s` is how long the step line is held high per pulse.
    """

    link: GpioLinkConfig | str  # type: ignore[valid-type]
    step_line: int = Field(ge=0)
    direction_line: int = Field(ge=0)
    steps_per_s: float = Field(gt=0)
    enable_line: int | None = Field(default=None, ge=0)
    enable_active_low: bool = True
    steps_per_unit: float | None = Field(default=None, gt=0)
    pulse_width_s: float = Field(default=0.0005, ge=0)

    def build(self, name: str, label: str | None = None) -> Stepper:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a chip before building")
        return Stepper(
            name,
            resolve(self.link),
            self.step_line,
            self.direction_line,
            self.steps_per_s,
            self.enable_line,
            self.enable_active_low,
            self.steps_per_unit,
            self.pulse_width_s,
            label=label,
        )


Stepper.config_type = StepperConfig  # the config is declared after the device it builds


__all__ = ["POSITION", "RATE", "Stepper", "StepperConfig"]
