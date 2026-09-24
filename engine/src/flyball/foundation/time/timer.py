"""`Timers`: one-shot and periodic calls on a rig's clock, whatever the clock is.

A liveness deadline, a write retry, a fault's wait: each is a short call due at
an instant of the rig's time. [Timers][flyball.foundation.time.timer.Timers]
keeps them in one heap and runs each when it is due, on one of two drivers:

- **a thread**, for a clock that runs by itself (wall time; a sim's
  `ScaledClock`): it waits on the clock (`Clock.wait`), so a scaled clock's
  deadlines come at the scaled time, and wakes early when an earlier call is
  added or the timers close;
- **the clock itself**, for one that moves only when told (a sim's
  `SteppedClock`: anything with `cancel` and `call_later`, or only a periodic
  `schedule`, used once): one call is kept
  on the clock at the earliest due time, and the calls run on whoever
  advances it, in time order with everything else scheduled there.

A call runs outside every lock of the timers', so it may take the rig's lock,
add or cancel calls, or close the timers. One that raises is logged and
counted (`errors`, `last_error`); a periodic call keeps its period, and the
thread survives it. A periodic call that falls more than a period behind (a
stall, a suspend) is resynchronised rather than fired back to back, and
counted in `missed`.

`close` cancels everything and waits a bounded time for a call in progress:
a call stuck in a driver is abandoned (the thread is a daemon), never waited
on for ever.
"""

from __future__ import annotations

import heapq
import itertools
import logging
from collections.abc import Callable
from threading import Event, Lock, Thread, current_thread
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .clock import Clock

log = logging.getLogger("flyball.timers")

CLOSE_JOIN_S = 1.0
"""How long `close` waits, by default, for a call in progress to return."""

WAKE_S = 0.5
"""The longest the thread waits, in real seconds, before looking at the clock again: a
scaled clock's speed may change while it waits."""


class Timer:
    """One call due on a [Timers][flyball.foundation.time.timer.Timers]: cancel it, or see it.

    Returned by `after` and `every`. Cancelling one that already ran (a
    one-shot) or was cancelled is a no-op, so a holder may cancel
    unconditionally.
    """

    __slots__ = ("_timers", "active", "due_ns", "fn", "name", "period_ns", "runs")

    def __init__(
        self,
        timers: Timers,
        due_ns: int,
        fn: Callable[[], object],
        period_ns: int | None,
        name: str,
    ) -> None:
        self._timers = timers
        self.due_ns = due_ns
        """When it runs next, on the clock's monotonic time."""
        self.fn = fn
        self.period_ns = period_ns
        """None: a one-shot."""
        self.name = name
        self.runs = 0
        """How many times it has run."""
        self.active = True
        """False once cancelled, or once a one-shot has started to run."""

    def cancel(self) -> None:
        """Do not run it again; a call already running finishes."""
        self._timers._cancel(self)

    def __repr__(self) -> str:
        what = "once" if self.period_ns is None else f"every {self.period_ns / 1e9:g} s"
        return f"Timer({self.name!r}, {what}, due {self.due_ns}, active={self.active})"


class Timers:
    """Calls due on `clock`: one-shot (`after`) and periodic (`every`).

    Safe from any thread. Nothing runs until the first call is added; the
    thread (for a clock that runs by itself) starts then, and is started
    again if it ever died.
    """

    def __init__(self, clock: Clock, name: str = "timers") -> None:
        self.clock = clock
        self.name = name
        self._lock = Lock()
        self._heap: list[tuple[int, int, Timer]] = []
        self._seq = itertools.count()
        self._wake = Event()
        self._closed = False
        self._thread: Thread | None = None
        self._stepped = callable(getattr(clock, "cancel", None)) and (
            callable(getattr(clock, "call_later", None))
            or callable(getattr(clock, "schedule", None))
        )
        self._armed: tuple[int, Any] | None = None
        """On a stepped clock: the due time and handle of the one call kept on the clock."""
        self._running = False
        """On a stepped clock: the due calls are being run now; they re-arm the clock after."""
        self.errors = 0
        """Calls that raised, in all."""
        self.last_error: BaseException | None = None
        self.missed = 0
        """Periods skipped by periodic calls that fell more than a period behind."""

    # region Adding and cancelling

    def after(self, seconds: float, fn: Callable[[], object], name: str = "") -> Timer:
        """Run `fn` once, `seconds` of the clock's time from now (0 or less: as soon as possible).

        Raises:
            RuntimeError: The timers are closed.
        """
        due = self.clock.monotonic_ns() + max(0, round(seconds * 1e9))
        return self._add(Timer(self, due, fn, None, name or _name_of(fn)))

    def every(
        self,
        seconds: float,
        fn: Callable[[], object],
        name: str = "",
        *,
        first_s: float | None = None,
    ) -> Timer:
        """Run `fn` every `seconds` of the clock's time; first after `first_s`, default one period.

        Raises:
            ValueError: `seconds` is not above zero.
            RuntimeError: The timers are closed.
        """
        if not seconds > 0:
            raise ValueError(f"a period must be above zero, not {seconds!r}")
        period = round(seconds * 1e9)
        first = period if first_s is None else max(0, round(first_s * 1e9))
        due = self.clock.monotonic_ns() + first
        return self._add(Timer(self, due, fn, period, name or _name_of(fn)))

    def _add(self, timer: Timer) -> Timer:
        with self._lock:
            if self._closed:
                raise RuntimeError(f"{self.name}: closed")
            earliest = self._heap[0][0] if self._heap else None
            heapq.heappush(self._heap, (timer.due_ns, next(self._seq), timer))
            sooner = earliest is None or timer.due_ns < earliest
            if self._stepped:
                if not self._running:
                    self._arm_locked()
            elif sooner:
                self._wake.set()
        if not self._stepped:
            self._supervise()
        return timer

    def _cancel(self, timer: Timer) -> None:
        with self._lock:
            timer.active = False  # left in the heap: skipped when it comes up

    @property
    def pending(self) -> int:
        """Calls still due: active one-shots and periodic calls."""
        with self._lock:
            return sum(1 for _, _, t in self._heap if t.active)

    # endregion

    # region Running

    def _due(self) -> list[Timer]:
        """Under the lock: pop what is due now (and drop what was cancelled); one-shots end."""
        now = self.clock.monotonic_ns()
        ready: list[Timer] = []
        while self._heap and (self._heap[0][0] <= now or not self._heap[0][2].active):
            _, _, timer = heapq.heappop(self._heap)
            if not timer.active:
                continue
            ready.append(timer)
            if timer.period_ns is None:
                continue  # out of the heap; `active` until it starts to run
            timer.due_ns += timer.period_ns
            if timer.due_ns <= now - timer.period_ns:  # more than a period behind: resync
                self.missed += 1
                timer.due_ns = now + timer.period_ns
            heapq.heappush(self._heap, (timer.due_ns, next(self._seq), timer))
        return ready

    def _call(self, timer: Timer) -> None:
        timer.runs += 1
        try:
            timer.fn()
        except Exception as error:
            self.errors += 1
            self.last_error = error
            log.exception("%s: %s raised", self.name, timer.name)

    def run_due(self) -> int:
        """Run every call due now, in time order, on the calling thread; how many ran.

        What the drivers do; a test on a clock with neither driver may call it.
        """
        ran = 0
        while True:
            with self._lock:
                if self._closed:
                    return ran
                ready = self._due()
            if not ready:
                return ran
            for timer in ready:
                with self._lock:
                    if not timer.active or self._closed:
                        continue  # cancelled since it came due
                    if timer.period_ns is None:
                        timer.active = False
                self._call(timer)
                ran += 1

    # endregion

    # region The stepped driver: one call kept on the clock

    def _arm_locked(self) -> None:
        """Keep one call on the clock at the earliest due time (under the lock)."""
        while self._heap and not self._heap[0][2].active:
            heapq.heappop(self._heap)
        earliest = self._heap[0][0] if self._heap else None
        if self._armed is not None:
            if earliest is not None and self._armed[0] <= earliest:
                return  # the one on the clock comes first anyway: it re-arms after
            self.clock.cancel(self._armed[1])  # type: ignore[attr-defined]
            self._armed = None
        if earliest is None:
            return
        delay = max(0, earliest - self.clock.monotonic_ns()) / 1e9
        call_later = getattr(self.clock, "call_later", None)
        if call_later is not None:
            handle = call_later(delay, self._stepped_wake)
        else:  # an older clock with only a periodic `schedule`: cancelled when it runs
            handle = self.clock.schedule(max(delay, 1e-9), self._stepped_wake)  # type: ignore[attr-defined]
        self._armed = (earliest, handle)

    def _stepped_wake(self) -> None:
        with self._lock:
            armed, self._armed = self._armed, None
            if armed is not None and not callable(getattr(self.clock, "call_later", None)):
                self.clock.cancel(armed[1])  # type: ignore[attr-defined]  # a one-shot
            if self._closed:
                return
            self._running = True
        try:
            self.run_due()
        finally:
            with self._lock:
                self._running = False
                if not self._closed:
                    self._arm_locked()

    # endregion

    # region The thread driver

    def _supervise(self) -> None:
        """Start the thread if none is running: the first call added, or after it died."""
        with self._lock:
            if self._closed or (self._thread is not None and self._thread.is_alive()):
                return
            self._thread = Thread(target=self._run, daemon=True, name=self.name)
            self._thread.start()

    def _run(self) -> None:
        while True:
            with self._lock:
                if self._closed:
                    return
                self._wake.clear()
                while self._heap and not self._heap[0][2].active:
                    heapq.heappop(self._heap)
                earliest = self._heap[0][0] if self._heap else None
            now = self.clock.monotonic_ns()
            if earliest is None or earliest > now:
                # A scaled clock's speed may change while waiting: look again at least
                # every `WAKE_S` of real time.
                cap = WAKE_S * float(getattr(self.clock, "speed", 1.0) or 1.0)
                wait = cap if earliest is None else min(cap, (earliest - now) / 1e9)
                self.clock.wait(self._wake, wait)
                continue
            self.run_due()

    # endregion

    def close(self, timeout: float = CLOSE_JOIN_S) -> bool:
        """Cancel every call, stop the thread; whether it stopped within `timeout` real seconds.

        A call in progress finishes (or is abandoned after `timeout`); none
        starts after. Idempotent. From inside a call, nothing is waited for.
        """
        with self._lock:
            self._closed = True
            for _, _, timer in self._heap:
                timer.active = False
            self._heap.clear()
            armed, self._armed = self._armed, None
            thread = self._thread
        self._wake.set()
        if armed is not None:
            self.clock.cancel(armed[1])  # type: ignore[attr-defined]
        if thread is None or thread is current_thread():
            return True
        thread.join(timeout)
        if thread.is_alive():
            log.warning("%s: a call did not return within %.1f s of close", self.name, timeout)
            return False
        return True

    @property
    def closed(self) -> bool:
        return self._closed


def _name_of(fn: Callable[..., object]) -> str:
    return getattr(fn, "__qualname__", None) or repr(fn)


__all__ = ["Timer", "Timers"]
