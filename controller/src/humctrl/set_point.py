from __future__ import annotations

from threading import Event, Thread
from typing import Any, ClassVar

from pydantic.alias_generators import to_snake

from humctrl.clock import Duration, Rate
from humctrl.signal import Signal, TimedSignal

#: Every registered generator, keyed by the tag it crosses the wire under.
SetPointGenerators: dict[str, type[SetPointGenerator]] = {}


class SetPointGenerator:
    """A reference trajectory. Registered by tag when subclassed."""

    tag: ClassVar[str] = ""

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

    def start(self, time: float, value: float) -> Signal | None:
        """Notify the generator that the trajectory is starting at ``time``."""

    def generate(self, time: float) -> float:
        """The set point at ``time``."""
        raise NotImplementedError

    def finished(self, time: float) -> bool:
        """Whether the trajectory has arrived and nothing further will change."""
        return False

    @property
    def running(self) -> bool:
        return True


class Hold(SetPointGenerator):
    """A fixed set point. The degenerate profile, and the default."""

    def __init__(self, duration: float | None = None) -> None:
        self.duration = duration

    def start(self, time: float, value: float) -> None:
        if self.duration is not None:
            self.end_time = time + self.duration
        self.value = value

    def generate(self, time: float) -> float:
        return self.value

    def finished(self, time: float) -> bool:
        # A hold has nowhere to arrive: a runner decides when it has waited long
        # enough, not the generator.
        return False


class LinearRamp(SetPointGenerator):
    """A set point walking from ``start`` to ``end`` between two instants."""

    pace: Rate | Duration
    end: float
    end_time: float
    _event: Event

    def __init__(
        self,
        pace: Rate | Duration,
        end: float,
    ) -> None:
        self.pace = pace
        self.end = end

    def start(self, time: float, value: float) -> Signal:
        dx = self.end - value
        if isinstance(self.pace, Rate):
            duration = abs(dx / self.pace.per_second)
        else:
            duration = float(self.pace)
        self.end_time = time + duration
        self.rate = duration or dx / duration
        self._event = TimedSignal(duration)
        return self._event

    def generate(self, time: float) -> float:
        if time >= self.end_time:
            self._event.set()
            return self.end
        return self.end - (self.end_time - time) * self.rate

    def finished(self, time: float) -> bool:
        return time >= self.end_time


def timed_event(duration: float) -> Event:
    event = Event()

    def thread():
        event.wait(duration)
        event.set()

    Thread(target=thread, daemon=True).start()
    return event
