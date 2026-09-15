from __future__ import annotations

from typing import Any, ClassVar

from pydantic.alias_generators import to_snake
from pydantic_core import core_schema

from flyball.core import Duration, Rate, Signal, Speed

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

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> core_schema.CoreSchema:
        """Serialise as ``{"tag": ...}``: a view of a running trajectory, not a way to build one."""
        return core_schema.json_or_python_schema(
            json_schema=core_schema.no_info_plain_validator_function(cls._reject),
            python_schema=core_schema.is_instance_schema(cls),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda g: {"tag": g.tag}, when_used="always"
            ),
        )

    @staticmethod
    def _reject(value: Any) -> Any:
        raise ValueError("a running trajectory cannot be built from the wire")

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


class LinearRampSetpoint(SetPointGenerator):
    """A set point walking from ``start`` to ``end`` between two instants."""

    signal: Signal
    pace: Speed | Duration
    end: float
    end_time: float
    per_second: float

    def __init__(self, pace: Speed | Duration, end: float) -> None:
        self.pace = pace
        self.end = end
        self.signal = Signal()

    def start(self, time: float, value: float) -> None:
        span = self.end - value
        duration = (
            abs(span / self.pace.per_second) if isinstance(self.pace, Rate) else self.pace.seconds
        )
        self.end_time = time + duration
        self.signal.set_timeout(duration)
        # Signed by the distance: the pace says how fast, never which way, so a
        # descending ramp needs the sign taken from the span.
        self.per_second = span / duration if duration > 0.0 else 0.0

    def generate(self, time: float) -> float:
        if time >= self.end_time:
            return self.end
        return self.end - (self.end_time - time) * self.per_second
