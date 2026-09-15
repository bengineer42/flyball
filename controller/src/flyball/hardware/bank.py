"""A bank of two-phase devices read together so their results share an instant.

Many sensors split a measurement into *trigger* (start converting) and
*collect* (wait, then read). Triggering every device before collecting any
means the conversions overlap: three sensors cost one conversion time, not
three, and their results are stamped within microseconds of each other.

The bank knows nothing about what a device returns. It maps each key to that
device's result, or to the exception that stopped it, and leaves the caller to
decide what a failure means.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Protocol


class TwoPhase[R](Protocol):
    """A device whose read splits into trigger and collect."""

    def trigger(self) -> int:
        """Start a conversion. Returns ``time.monotonic_ns()`` at the trigger."""
        ...

    def collect(self, trigger_ns: int, stamp_ns: int) -> R:
        """Wait out the conversion started at ``trigger_ns`` and read it.

        ``stamp_ns`` is the trigger instant in the caller's epoch, for the
        result to carry.
        """
        ...


class Bank[K, R]:
    """Trigger every device, then collect every device: one conversion time for all."""

    __slots__ = ("devices",)

    devices: dict[K, TwoPhase[R]]

    def __init__(self, devices: Mapping[K, TwoPhase[R]]) -> None:
        self.devices = dict(devices)

    def read_device(self, time_ns: int, key: K) -> R | Exception | None:
        try:
            offset_ns = time_ns - time.monotonic_ns()
            trigger_ns = self.devices[key].trigger()
            return self.devices[key].collect(trigger_ns, trigger_ns + offset_ns)
        except Exception as e:
            return e

    def read_devices(self, time_ns: int, keys: list[K]) -> dict[K, R | Exception]:
        offset_ns = time_ns - time.monotonic_ns()
        triggered: list[tuple[K, TwoPhase[R], int]] = []
        results: dict[K, R | Exception] = {}
        for key in keys:
            try:
                triggered.append((key, self.devices[key], self.devices[key].trigger()))
            except Exception as e:  # one device must not stop the bank
                results[key] = e

        for key, device, trigger_ns in triggered:
            try:
                results[key] = device.collect(trigger_ns, trigger_ns + offset_ns)
            except Exception as e:
                results[key] = e
        return results

    def read_bank(self, time_ns: int) -> dict[K, R | Exception]:
        return self.read_devices(time_ns, list(self.devices.keys()))
