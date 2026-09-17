"""The I2C bus protocol, and a multiplexer that presents each lane as a bus.

A driver takes an [I2CBus][flyball.hardware.i2c.I2CBus] and cannot tell the
root bus from a lane behind an [I2CMux][flyball.hardware.i2c.I2CMux]. Lane
selection happens inside the transaction, under the mux's lock, so drivers on
different lanes cannot interleave.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager, suppress
from threading import RLock
from typing import Protocol, runtime_checkable

TCA9548_ADDRESS = 0x70


@runtime_checkable
class I2CBus(Protocol):
    """The subset of `busio.I2C` this package uses. Blinka is untyped; fakes stay substitutable."""

    def try_lock(self) -> bool: ...
    def unlock(self) -> None: ...
    def writeto(self, address: int, buffer: bytes) -> None: ...
    def readfrom_into(self, address: int, buffer: bytearray) -> None: ...


class I2CMux:
    """A TCA9548-style multiplexer: one control byte selects the live lanes.

    Its lock makes select-then-transfer atomic against other lanes. `lane(n)`
    returns an [I2CBus][flyball.hardware.i2c.I2CBus].
    """

    __slots__ = ("_address", "_depth", "_i2c", "_lock", "_selected")

    def __init__(self, i2c: I2CBus, address: int = TCA9548_ADDRESS) -> None:
        self._i2c = i2c
        self._address = address
        self._lock = RLock()
        self._selected: int | None = None
        self._depth = 0

    @property
    def i2c(self) -> I2CBus:
        return self._i2c

    def lane(self, lane: int) -> MuxedLane:
        if not 0 <= lane < 8:
            raise ValueError(f"Mux lane must be 0-7, not {lane}")
        return MuxedLane(self, lane)

    def acquire(self, lane: int) -> I2CBus:
        """Take the mux and root bus with `lane` routed. Re-entrant; selects only on change."""
        self._lock.acquire()
        try:
            if self._depth == 0:
                self._i2c.try_lock()
            if self._selected != lane:
                self._i2c.writeto(self._address, bytes((1 << lane,)))
                self._selected = lane
            self._depth += 1
        except BaseException:
            self._lock.release()
            raise
        return self._i2c

    def release(self) -> None:
        """Undo one `acquire`. The outermost release deselects every lane."""
        try:
            self._depth -= 1
            if self._depth == 0:
                with suppress(OSError):
                    self._i2c.writeto(self._address, b"\x00")
                self._selected = None
                self._i2c.unlock()
        finally:
            self._lock.release()

    @contextmanager
    def select(self, lane: int) -> Generator[I2CBus]:
        """`acquire`/`release` as a context manager, for one transaction."""
        bus = self.acquire(lane)
        try:
            yield bus
        finally:
            self.release()


class MuxedLane:
    """One [I2CMux][flyball.hardware.i2c.I2CMux] lane as an [I2CBus][flyball.hardware.i2c.I2CBus].

    Each transfer selects the lane first. `try_lock`/`unlock` bracket a
    multi-transfer sequence so the lane stays selected between them.
    """

    __slots__ = ("_lane", "_mux")

    def __init__(self, mux: I2CMux, lane: int) -> None:
        self._mux = mux
        self._lane = lane

    @property
    def lane(self) -> int:
        return self._lane

    def try_lock(self) -> bool:
        self._mux.acquire(self._lane)
        return True

    def unlock(self) -> None:
        self._mux.release()

    def writeto(self, address: int, buffer: bytes) -> None:
        with self._mux.select(self._lane) as bus:
            bus.writeto(address, buffer)

    def readfrom_into(self, address: int, buffer: bytearray) -> None:
        with self._mux.select(self._lane) as bus:
            bus.readfrom_into(address, buffer)
