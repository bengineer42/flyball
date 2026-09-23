"""Hardware PWM over sysfs: `/sys/class/pwm/pwmchipN`. No library needed."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

from flyball.foundation.config import Config
from pydantic import Field


@runtime_checkable
class PwmLink(Protocol):
    """A chip of channels; each is set as a period and a duty, both in nanoseconds."""

    def configure(self, channel: int, period_ns: int, duty_ns: int) -> None: ...

    def enable(self, channel: int, on: bool) -> None: ...


class FakePwm:
    """Remembers the last period, duty and enable per channel, and every configure."""

    def __init__(self) -> None:
        self.channels: dict[int, tuple[int, int]] = {}
        self.enabled: dict[int, bool] = {}
        self.history: list[tuple[int, int, int]] = []

    def configure(self, channel: int, period_ns: int, duty_ns: int) -> None:
        if not 0 <= duty_ns <= period_ns:
            raise ValueError(f"duty {duty_ns} ns outside period {period_ns} ns")
        self.channels[channel] = (period_ns, duty_ns)
        self.history.append((channel, period_ns, duty_ns))

    def enable(self, channel: int, on: bool) -> None:
        self.enabled[channel] = on


class FakePwmConfig(Config[PwmLink], tag="fake_pwm"):
    def build(self) -> PwmLink:
        return FakePwm()


class SysfsPwm:
    """`/sys/class/pwm/pwmchip<chip>`: exports a channel on first use.

    Duty is written before period when shrinking and after when growing, as
    the kernel refuses a duty longer than the current period.
    """

    def __init__(self, chip: int = 0, root: str | Path = "/sys/class/pwm") -> None:
        self.chip = chip
        self.root = Path(root) / f"pwmchip{chip}"
        if not self.root.is_dir():
            raise OSError(f"{self.root} does not exist; is the PWM overlay enabled?")
        self._periods: dict[int, int] = {}
        self._lock = threading.Lock()

    def _channel(self, channel: int) -> Path:
        path = self.root / f"pwm{channel}"
        if not path.is_dir():
            (self.root / "export").write_text(str(channel))
            # export_store() creates period/duty_cycle/enable synchronously (kernel
            # drivers/pwm/core.c pwm_export_child -> device_register), but their
            # group/perm is granted by udev off the uevent it fires afterwards, so
            # `enable` can be briefly unwritable right after export.
            deadline = time.monotonic() + 1.0
            while not os.access(path / "enable", os.W_OK) and time.monotonic() < deadline:
                time.sleep(0.01)
        if channel not in self._periods:
            self._periods[channel] = int((path / "period").read_text())
        return path

    def configure(self, channel: int, period_ns: int, duty_ns: int) -> None:
        if not 0 <= duty_ns <= period_ns:
            raise ValueError(f"duty {duty_ns} ns outside period {period_ns} ns")
        with self._lock:
            path = self._channel(channel)
            if period_ns < self._periods.get(channel, 0):
                (path / "duty_cycle").write_text(str(duty_ns))
                (path / "period").write_text(str(period_ns))
            else:
                (path / "period").write_text(str(period_ns))
                (path / "duty_cycle").write_text(str(duty_ns))
            self._periods[channel] = period_ns

    def enable(self, channel: int, on: bool) -> None:
        with self._lock:
            (self._channel(channel) / "enable").write_text("1" if on else "0")


class PwmConfig(Config[PwmLink], tag="pwm"):
    """A kernel PWM chip: `chip = 0` is `/sys/class/pwm/pwmchip0`."""

    chip: int = Field(default=0, ge=0)
    root: str = "/sys/class/pwm"

    def build(self) -> PwmLink:
        return SysfsPwm(self.chip, self.root)


PWM_LINKS = (FakePwmConfig, PwmConfig)
PwmLinkConfig = Config.union(*PWM_LINKS)

__all__ = [
    "PWM_LINKS",
    "FakePwm",
    "FakePwmConfig",
    "PwmConfig",
    "PwmLink",
    "PwmLinkConfig",
    "SysfsPwm",
]
