"""A dosing pump as a device: `dispense(volume_ml)` on top of a `pwm_channel` or `gpio_line`.

**Never run against real hardware.** This driver has only been exercised against the fake
links in `linux/tests`; the calibration model, the timing, and the "always stop" guarantee
below are unverified on an actual peristaltic pump.

A peristaltic dosing pump is driven one of two ways: a DC motor whose speed is set by PWM
duty (a `pwm_channel` underneath, `drive` 0 to 1), or a simple relay-switched pump that is
either on or off (a `gpio_line` underneath). This first version supports both, because the
underlying primitive is already a device either way and there is nothing dosing-specific
about which one drives the motor -- only the calibration differs (ml/s at a given drive
fraction vs. ml/s while on). Stepper-driven pumps (step/direction pulses, ml per pulse) are
out of scope: there is no existing pulse-generating link in `flyball_linux` to drive steps
from, so supporting that would mean inventing a new primitive rather than layering on one
that exists, which this first version deliberately avoids.

Calibration is a single `ml_per_s` figure: how many ml/s the pump delivers at full drive (or
while on, for the GPIO case). This is deliberately the simplest possible model -- linear,
one point, no flow curve -- and is a placeholder until real calibration data exists.

`dispense` declares `writes=("pump",)`: it moves the private child pump, which no
demand of this device says. Nothing on the rig can drive that child today, so the
declaration refuses nothing yet; it is there for when the child's writes go through the
rig.

`dispense` is a long command (`@command(long=True)`): it runs the pump for
`volume_ml / ml_per_s` seconds of the rig's time and blocks its caller, but the rig runs it
off its lock, so polling, deliveries and a `stop` carry on meanwhile. It waits on
`Device.wait`, so `stop` (which calls `Device.cancel`) ends the dose at once, and a scaled or
stepped sim clock scales or steps the dose. Critically for a real dosing skid, the pump is
switched off in a `finally`, so a stop, or an exception partway through (a hardware error
from the underlying link), never leaves it running.
"""

from __future__ import annotations

from typing import Literal

from flyball.foundation.device import (
    Access,
    Committable,
    DriverConfig,
    Role,
    Setting,
    SignalSpec,
    command,
)
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.dimensions import Volume
from flyball.foundation.quantities.si import Second
from pydantic import Field, model_validator

from flyball_linux.devices.gpio import GpioLine, GpioLineConfig
from flyball_linux.devices.pwm import PwmChannel, PwmChannelConfig

Milliliter = Volume.unit("millilitre", "ml", 1e-6)
VOLUME = Quantity("volume", Milliliter)
RATE = Quantity("rate", Milliliter / Second)  # type: ignore[operator]

Drive = Literal["pwm", "gpio"]


class DosingPump(Committable):
    """`dispense(volume_ml)`: run the underlying pump for a calculated duration, then stop it.

    `ml_per_s` is the delivery rate at full drive (`pwm`) or while on (`gpio`): a single
    linear calibration point, not a curve. `max_dispense_ml` bounds one call, so a bad
    request cannot run the pump indefinitely (the timed run is itself already bounded, but
    the volume is the operator-facing quantity and worth its own limit).
    """

    ml_per_s = Setting("ml_per_s", "Delivery rate at full drive", RATE)

    def __init__(
        self,
        name: str,
        pump: PwmChannel | GpioLine,
        ml_per_s: float,
        drive_fraction: float = 1.0,
        max_dispense_ml: float | None = None,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        if ml_per_s <= 0:
            raise ValueError(f"{name}: ml_per_s must be positive")
        if not 0.0 < drive_fraction <= 1.0:
            raise ValueError(f"{name}: drive_fraction must be in (0, 1]")
        if max_dispense_ml is not None and max_dispense_ml <= 0:
            raise ValueError(f"{name}: max_dispense_ml must be positive")
        self.pump = pump
        self.kind: Drive = "pwm" if isinstance(pump, PwmChannel) else "gpio"
        self.drive_fraction = drive_fraction
        self.max_dispense_ml = max_dispense_ml
        self.bind((
            SignalSpec(
                name="dispensed_ml",
                quantity=VOLUME,
                access=Access.RP,
                role=Role.READOUT,
                initial=0.0,
            ),
        ))
        self.ml_per_s.push(ml_per_s)

    @property
    def config(self) -> DosingPumpConfig:
        return DosingPumpConfig(
            pump="",
            ml_per_s=self.ml_per_s.value,
            drive_fraction=self.drive_fraction,
            max_dispense_ml=self.max_dispense_ml,
        )

    def duration_s(self, volume_ml: float) -> float:
        """How long `dispense(volume_ml)` runs the pump for, at the current calibration."""
        return volume_ml / self.ml_per_s.value

    def _run(self, on: bool) -> None:
        if self.kind == "pwm":
            assert isinstance(self.pump, PwmChannel)
            self.pump.write_signal(self.pump.signals["drive"], self.drive_fraction if on else 0.0)
        else:
            assert isinstance(self.pump, GpioLine)
            if on:
                self.pump.on()
            else:
                self.pump.off()

    @command(long=True, writes=("pump",))
    def dispense(self, volume_ml: float) -> None:
        """Run the pump for `volume_ml / ml_per_s` seconds, then stop it.

        Stops the pump even if stopped early or the hardware call raises.
        `dispensed_ml` grows by what ran: the whole volume, or the share of
        it dosed before a `stop`.
        """
        if volume_ml <= 0:
            raise ValueError("volume_ml must be positive")
        if self.max_dispense_ml is not None and volume_ml > self.max_dispense_ml:
            raise ValueError(
                f"volume_ml {volume_ml} exceeds max_dispense_ml {self.max_dispense_ml}"
            )
        duration_s = self.duration_s(volume_ml)
        clock = self.clock_source()
        started = clock.monotonic()
        try:
            self._run(True)
            stopped = self.wait(duration_s)
        finally:
            self._run(False)
        ran_s = min(duration_s, clock.monotonic() - started) if stopped else duration_s
        dosed = volume_ml * ran_s / duration_s
        self.signals["dispensed_ml"].push(self.signals["dispensed_ml"].value + dosed)

    @command(stops=True)
    def stop(self) -> None:
        """Stop the pump immediately, whatever it is doing: a dose in progress ends now.

        The device's stop: a rig stop runs it.
        """
        self.cancel()
        self._run(False)


class DosingPumpConfig(DriverConfig[DosingPump], type="dosing_pump"):
    """`driver: dosing_pump`: `{ pump, ml_per_s }`, `pump` a nested `pwm_channel` or `gpio_line`.

    `drive_fraction` (pwm only) is the duty the pump runs at during a dispense, default full
    drive. `max_dispense_ml` optionally caps a single `dispense` call.
    """

    pump: PwmChannelConfig | GpioLineConfig | str  # type: ignore[valid-type]
    ml_per_s: float = Field(gt=0, description="Delivery rate at full drive (pwm) or on (gpio).")
    drive_fraction: float = Field(default=1.0, gt=0.0, le=1.0)
    max_dispense_ml: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _drive_fraction_needs_pwm(self) -> DosingPumpConfig:
        if not isinstance(self.pump, PwmChannelConfig) and self.drive_fraction < 1.0:
            raise ValueError("`drive_fraction` only applies to a `pwm_channel` pump")
        return self

    def build(self, name: str, label: str | None = None) -> DosingPump:
        if isinstance(self.pump, str):
            raise TypeError(f"pump {self.pump!r} must be resolved to a chip before building")
        pump = self.pump.build(f"{name}_pump")  # private: a key, never on the rig
        if not isinstance(pump, (PwmChannel, GpioLine)):
            raise TypeError(f"{name}: pump must build a PwmChannel or GpioLine")
        return DosingPump(
            name,
            pump,
            self.ml_per_s,
            self.drive_fraction,
            self.max_dispense_ml,
            label=label,
        )


DosingPump.config_type = DosingPumpConfig  # the config is declared after the device it builds


__all__ = ["RATE", "VOLUME", "DosingPump", "DosingPumpConfig", "Milliliter"]
