from __future__ import annotations

from typing import Any, ClassVar

from pydantic.alias_generators import to_snake

from humctrl.core import Duration, Rate, Signal, Speed

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

    def start(self, time_ns: float, value: float) -> None:
        """Bind to the rig: the clock origin and where the process is now.

        Args:
            time: The instant the trajectory begins.
            value: The process value at that instant, for generators that start
                from wherever the rig happens to be.
        """

    def generate(self, time_ns: float) -> float:
        """The set point at ``time``."""
        raise NotImplementedError


class LinearRampSetpoint(SetPointGenerator):
    """A set point walking from ``start`` to ``end`` between two instants."""

    pace: Speed | Duration
    end: float
    end_ns: int
    per_ns: float

    def __init__(self, pace: Speed | Duration, end: float) -> None:
        self.pace = pace
        self.end = end

    def start(self, time_ns: int, value: float) -> None:
        if isinstance(self.pace, Rate):
            self.per_ns = self.pace.per_nanosecond
            self.end_ns = time_ns + abs(round((self.end - value) / self.per_ns))
        else:
            if self.pace.nanoseconds <= 0:
                self.end_ns = time_ns
                self.per_ns = 0.0
            else:
                self.end_ns = time_ns + self.pace.nanoseconds
                self.per_ns = (self.end - value) / self.pace.nanoseconds

    def generate(self, time_ns: int) -> float:
        if time_ns >= self.end_ns:
            return self.end
        return self.end - (self.end_ns - time_ns) * self.per_ns
