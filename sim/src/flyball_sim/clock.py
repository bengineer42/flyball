"""Clocks for a rig that does not run on wall time.

[SteppedClock][flyball.sim.clock.SteppedClock] moves only when told, for a
test that wants exact instants. [ScaledClock][flyball.sim.clock.ScaledClock]
runs at a multiple of real time, for watching a slow plant settle in
minutes rather than hours. Both stamp samples and time out waits in their
own time, because everything that waits goes through the clock.
"""

from __future__ import annotations

import heapq
import time
from collections.abc import Callable
from threading import Event, Lock, get_ident

from flyball.foundation.time import Clock


class SteppedClock(Clock):
    """A clock that only moves when told to, so a test ticks a rig at exact instants.

    Anything periodic on the rig -- a reader's poll -- is scheduled on the
    clock rather than on a thread, and runs as `advance` passes its due
    times, in order. `sleep` and `wait` advance the clock instead of
    blocking, so a program's holds pass at once *with* every poll in between
    having happened: a two-hour firing runs, physics and all, in the time
    the arithmetic takes.
    """

    __slots__ = ("_advance_lock", "_advancer", "_due", "_elapsed_ns", "_lock", "_seq")

    def __init__(self, seconds: float | int | None = None) -> None:
        self._elapsed_ns = 0
        self._due: list[tuple[int, int, int | None, Callable[[], object]]] = []
        self._seq = 0
        self._lock = Lock()
        """Guards the schedule alone, and is never held while a scheduled call runs: a call
        may schedule or cancel, and another thread may, while the clock advances."""
        self._advance_lock = Lock()
        """One advance at a time; a second thread's waits for the first to finish."""
        self._advancer: int | None = None
        """The thread advancing now, so a scheduled call that tries to advance is refused."""
        super().__init__(seconds)

    def monotonic_ns(self) -> int:
        return self._elapsed_ns

    def now_ns(self) -> int:
        return self.start_time_ns + self._elapsed_ns

    # region Scheduling

    def schedule(self, period_s: float, fn: Callable[[], object]) -> int:
        """Run `fn` every `period_s` of clock time, first after one period; returns a handle."""
        if period_s <= 0:
            raise ValueError("period must be positive")
        period_ns = round(period_s * 1e9)
        return self._push(period_ns, period_ns, fn)

    def call_later(self, delay_s: float, fn: Callable[[], object]) -> int:
        """Run `fn` once, `delay_s` of clock time from now (0: on the next advance); a handle."""
        return self._push(max(0, round(delay_s * 1e9)), None, fn)

    def _push(self, delay_ns: int, period_ns: int | None, fn: Callable[[], object]) -> int:
        with self._lock:
            self._seq += 1
            heapq.heappush(self._due, (self._elapsed_ns + delay_ns, self._seq, period_ns, fn))
            return self._seq

    def cancel(self, handle: int) -> None:
        with self._lock:
            self._due = [entry for entry in self._due if entry[1] != handle]
            heapq.heapify(self._due)

    @property
    def scheduled(self) -> int:
        return len(self._due)

    # endregion

    def advance(self, seconds: float) -> int:
        """Move forward, running everything due on the way, in time order; return the new `now_ns`.

        Raises:
            RuntimeError: Called from inside something the clock is running.
        """
        if seconds < 0:
            raise ValueError("a clock does not go backwards")
        if self._advancer == get_ident():
            raise RuntimeError("the clock is already advancing; a scheduled call cannot advance it")
        with self._advance_lock:
            self._advancer = get_ident()
            try:
                target = self._elapsed_ns + round(seconds * 1e9)
                while True:
                    with self._lock:
                        if not self._due or self._due[0][0] > target:
                            break
                        due, seq, period_ns, fn = heapq.heappop(self._due)
                        self._elapsed_ns = due
                        if period_ns is not None:
                            heapq.heappush(self._due, (due + period_ns, seq, period_ns, fn))
                    fn()
                with self._lock:
                    self._elapsed_ns = max(self._elapsed_ns, target)
            finally:
                self._advancer = None
        return self.now_ns()

    def sleep(self, seconds: float) -> None:
        self.advance(max(0.0, seconds))

    def wait(self, event: Event, timeout: float | None = None) -> bool:
        """With a timeout, step past it at once; without one there is nothing to step to: block."""
        if timeout is None:
            return event.wait()
        if not event.is_set():
            self.advance(timeout)
        return event.is_set()


class ScaledClock(Clock):
    """Real time multiplied by `speed`: at 60, a simulated minute passes every second.

    The speed can change while running; the clock's time is continuous
    across the change. Waits scale too, so a reader polling every second
    polls sixty times a second at 60x and a ten-minute hold takes ten.
    """

    __slots__ = ("_base_real_ns", "_base_scaled_ns", "_lock", "_speed")

    def __init__(
        self,
        speed: float = 1.0,
        seconds: float | int | None = None,
        nanoseconds: int | None = None,
    ) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive")
        self._lock = Lock()
        self._speed = float(speed)
        self._base_real_ns = time.monotonic_ns()
        self._base_scaled_ns = self._base_real_ns
        super().__init__(seconds, nanoseconds)

    @property
    def speed(self) -> float:
        return self._speed

    def set_speed(self, speed: float) -> None:
        """Change the rate from now on; time already passed stays passed."""
        if speed <= 0:
            raise ValueError("speed must be positive")
        with self._lock:
            now_real = time.monotonic_ns()
            self._base_scaled_ns += round((now_real - self._base_real_ns) * self._speed)
            self._base_real_ns = now_real
            self._speed = float(speed)

    def monotonic_ns(self) -> int:
        with self._lock:
            return self._base_scaled_ns + round(
                (time.monotonic_ns() - self._base_real_ns) * self._speed
            )

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds / self._speed)

    def wait(self, event: Event, timeout: float | None = None) -> bool:
        return event.wait(None if timeout is None else timeout / self._speed)


__all__ = ["ScaledClock", "SteppedClock"]
