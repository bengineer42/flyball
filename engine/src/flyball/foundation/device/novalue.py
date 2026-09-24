"""No value: what a reading carries when it has no number, and why.

A reading's value is a number (or a mode, a record) or a
[NoValue][flyball.foundation.device.novalue.NoValue]: the signal was read, or
the rig judged it, and there is nothing a consumer may use. Nothing
downstream substitutes a number for it: a limit that follows it is unknown,
a controller regulating on it freezes, a settle wait on it is not met, and
the chart breaks.

Every signal has one **quality**, the first that matches of:

- `stale` with reason `device_offline` (the device's reads are failing);
- `pending` (nothing has been read on it yet);
- `stale` with another reason (`write_failed`: an echo demand whose device's writes fail);
- `invalid` / `not_applicable` (the newest reading is a no-value of that kind);
- `ok`.

`pending` and `not_applicable` are benign; `invalid` and `stale` are faults.
An `ok` value may carry **caveats**, which annotate it and gate nothing
(`at_limit`: it sits on an end the sensor or the clamp railed at).

What a driver yields (the driver contract):

- **leaves a signal out of a sample** (or pushes `None`): not read this time;
  the last value stands.
- **[invalid][flyball.foundation.device.novalue.invalid]`(reason, side)`**:
  read, but not a valid measurement -- a sensor's "no measurement", a NAMUR
  fault current, a CRC failure on one value of several. A no-value reading
  of quality `invalid`.
- **[not_applicable][flyball.foundation.device.novalue.not_applicable]`(reason)`**:
  the quantity is undefined now (a blend's humidity with no flow). Quality
  `not_applicable`.
- **`None`, NaN or an infinity inside a sample**: a value it could not
  produce; normalised to `invalid("no value")` / `invalid("not finite")`.
- **[railed][flyball.foundation.device.novalue.railed]`(value, "high")`**: a
  usable value at the end of what the sensor can report; the value, with
  the caveat `at_limit: high`.
- **raises `HardwareError`**: the transport failed and nothing was read. It
  counts toward `reads.fail_after`; at it, the device is `offline`.

A no-value is a good read of an absent value: it never counts toward the
failure budget. `stale` is the rig's, never a driver's.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..errors import NotReadyError


class Quality(StrEnum):
    """What a signal's value is worth now: `ok`, or why there is none."""

    OK = "ok"
    PENDING = "pending"
    """Nothing read yet. Benign: a controller on it waits, nothing fires."""
    NOT_APPLICABLE = "not_applicable"
    """The quantity is undefined now (shown as "n/a"). Benign."""
    INVALID = "invalid"
    """Read, but not a valid measurement. A fault."""
    STALE = "stale"
    """The rig no longer trusts the last value; `reason` says why. A fault."""

    @property
    def fault(self) -> bool:
        """`invalid` and `stale`: a fault. `pending` and `not_applicable` are benign."""
        return self in (Quality.INVALID, Quality.STALE)


class Reason(StrEnum):
    """Why a signal is `stale`: the rig's own reasons."""

    DEVICE_OFFLINE = "device_offline"
    """Its device's reads fail: the device holds `offline`."""
    DEVICE_HUNG = "device_hung"
    SILENT = "silent"
    NEVER_READ = "never_read"
    LAST_READ = "last_read"
    WRITE_FAILED = "write_failed"
    """An echo demand whose device's writes fail: what the device holds is not known."""


class Readback(StrEnum):
    """Where a demand's reading comes from."""

    ECHO = "echo"
    """The value the rig committed, pushed back as the reading (the default): it is known only
    while writes succeed, so it is `stale(write_failed)` while the device's writes fail."""
    SENSED = "sensed"
    """Read from the hardware: its reading is a measurement, stale when the reads are."""


class OnNoValue(StrEnum):
    """What a banded signal does when it has no value because of a fault (`invalid`, `stale`)."""

    FIRE = "fire"
    """Raise `band_unknown` after the signal's grace: counted in health `alarms.unknown`."""
    IGNORE = "ignore"
    """Show the band as unknown; raise nothing."""


@dataclass(frozen=True, slots=True)
class NoValue:
    """The value of a reading that has none: its quality, and the reason given.

    Never a number, never `None` (which a push reads as "nothing to push").
    It has no truth value: `if reading.value:` on one is a bug, and raises.
    """

    quality: Quality
    reason: str = ""
    """A short machine-readable why: `"ne43_low"`, `"no_flow"`, a stale `Reason`."""
    side: str | None = None
    """`"low"` or `"high"` when the driver knows which way it failed (a NAMUR fault current)."""

    def __post_init__(self) -> None:
        if self.quality in (Quality.OK, Quality.PENDING):
            raise ValueError(f"a no-value cannot be {self.quality!r}: that has a value, or none")
        if self.side not in (None, "low", "high"):
            raise ValueError(f"side {self.side!r}: low, high or None")

    def __bool__(self) -> bool:
        raise TypeError("NoValue has no truth value: test `reading.usable`")

    def __repr__(self) -> str:
        why = f", {self.reason!r}" if self.reason else ""
        return f"NoValue({self.quality.value}{why})"


def invalid(reason: str = "", side: str | None = None) -> NoValue:
    """Read, but not a valid measurement: a driver's no-value that is a fault."""
    return NoValue(Quality.INVALID, reason, side)


def not_applicable(reason: str = "") -> NoValue:
    """The quantity is undefined now: a driver's benign no-value."""
    return NoValue(Quality.NOT_APPLICABLE, reason)


def stale(reason: Reason) -> NoValue:
    """The rig's: the last value is no longer trusted."""
    return NoValue(Quality.STALE, reason.value)


@dataclass(frozen=True, slots=True)
class Railed:
    """A usable value the sensor reports at one end of what it can: `at_limit` on the reading.

    What a driver yields in place of the bare number; the rig takes the number and marks the
    reading. Make one with [railed][flyball.foundation.device.novalue.railed].
    """

    value: Any
    side: str
    """`"low"` or `"high"`."""


def railed(value: Any, side: str) -> Railed:
    """`value`, marked as sitting at the `side` (`"low"` / `"high"`, or a `Limit`) end."""
    side = str(getattr(side, "value", side))
    if side not in ("low", "high"):
        raise ValueError(f"side {side!r}: low or high")
    return Railed(value, side)


class NoValueError(NotReadyError):
    """A value was asked of a signal whose newest reading has none.

    It is `invalid`, `stale` or `not_applicable`. Nothing substitutes one; the caller holds
    or refuses.
    """

    def __init__(self, address: str, value: NoValue) -> None:
        self.address = address
        self.no_value = value
        why = f": {value.reason}" if value.reason else ""
        super().__init__(f"'{address}' has no value: {value.quality.value}{why}")


__all__ = [
    "NoValue",
    "NoValueError",
    "OnNoValue",
    "Quality",
    "Railed",
    "Readback",
    "Reason",
    "invalid",
    "not_applicable",
    "railed",
    "stale",
]
