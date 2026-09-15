from flyball.core.clock import Clock


class SteppedClock(Clock):
    """A clock that only moves when told to, so a test ticks a rig at exact instants.

    Anything scheduled on real time (`PeriodicLoop`) still runs on real time;
    drive such a rig with `rig.read(reader)` by hand.
    """

    __slots__ = ("_elapsed_ns",)

    def __init__(self, seconds: float | int | None = None) -> None:
        super().__init__(seconds)
        self._elapsed_ns = 0

    def now_ns(self) -> int:
        return self.start_time_ns + self._elapsed_ns

    def advance(self, seconds: float) -> int:
        """Move forward; return the new `now_ns`."""
        if seconds < 0:
            raise ValueError("a clock does not go backwards")
        self._elapsed_ns += round(seconds * 1e9)
        return self.now_ns()
