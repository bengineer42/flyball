from __future__ import annotations

from typing import Any, ClassVar

from pydantic.alias_generators import to_snake

from humctrl.clock import Duration, Rate
from humctrl.signal import Signal, TimedSignal

#: Every registered generator, keyed by the tag it crosses the wire under.
SetPointGenerators: dict[str, type[SetPointGenerator]] = {}


class SetPointGenerator:
    """A reference trajectory. Registered by tag when subclassed."""

    tag: ClassVar[str] = ""
    signal: Signal

    def __init_subclass__(
        cls, tag: str | None = None, register: bool = True, **kwargs: Any
    ) -> None:
        super().__init_subclass__(**kwargs)
        cls.tag = tag or cls.__dict__.get("tag") or to_snake(cls.__name__)
        if not register:
            return
        clash = SetPointGenerators.get(cls.tag)
        if clash is not None and (clash.__module__, clash.__qualname__) != (
            cls.__module__,
            cls.__qualname__,
        ):
            raise ValueError(f"tag {cls.tag!r} is already {clash.__name__}")
        SetPointGenerators[cls.tag] = cls

    def start(self, time: float, value: float) -> None:
        """Bind to the rig: the clock origin and where the process is now.

        Args:
            time: The instant the trajectory begins.
            value: The process value at that instant, for generators that start
                from wherever the rig happens to be.
        """

    def generate(self, time: float) -> float:
        """The set point at ``time``."""
        raise NotImplementedError


class LinearRamp(SetPointGenerator):
    """A set point walking from ``start`` to ``end`` between two instants."""

    pace: Rate | Duration
    end: float
    end_time: float
    rate: float

    def __init__(self, pace: Rate | Duration, end: float) -> None:
        self.pace = pace
        self.end = end

    def duration_from(self, value: float) -> float:
        """How long this ramp takes, starting from ``value``."""
        if not isinstance(self.pace, Rate):
            return float(self.pace)
        # The direction comes from the distance, so the rate's sign is ignored.
        return abs((self.end - value) / self.pace.per_second)

    def start(self, time: float, value: float) -> None:
        duration = self.duration_from(value)
        self.signal = TimedSignal(duration)
        self.end_time = time + duration
        # Already there: arrive immediately rather than dividing by zero.
        self.rate = 0.0 if duration == 0 else (self.end - value) / duration

    def generate(self, time: float) -> float:
        if time >= self.end_time:
            return self.end
        # Anchored on the target, so accumulated timing error cannot drift the
        # trajectory away from where it must land.
        return self.end - (self.end_time - time) * self.rate
