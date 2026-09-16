from __future__ import annotations

from inspect import signature
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_snake
from pydantic_core import core_schema

from flyball.core import Duration, Rate, Signal, Speed
from flyball.core.model import ModelOf, creation_model

SetPointGenerators: dict[str, type[SetPointGenerator]] = {}
"""Every registered generator, keyed by the tag it crosses the wire under."""


class GeneratorConfig(BaseModel):
    """How a generator was specified: its constructor arguments and its tag.

    The buildable side of a trajectory. A running one is only ever viewed,
    as a [Trajectory][flyball.control.setpoint.Trajectory].
    """

    model_config = ConfigDict(extra="forbid")

    generator: ClassVar[type]
    init_names: ClassVar[tuple[str, ...]] = ()

    tag: str

    def build(self) -> Any:
        return self.generator(**{name: getattr(self, name) for name in self.init_names})


class Trajectory(BaseModel):
    """A running generator as a client sees it: where it set off, where it heads, when it lands.

    `to` is None for a trajectory with no destination (a sine, say);
    `end_time_ns` for one that never lands -- no destination, or one it
    only ever approaches. The start is always known: it is where the loop
    was when the generator was started, and nothing else on the wire keeps it.
    """

    tag: str
    start: float
    """The setpoint the trajectory set off from, in the channel's unit."""
    start_time_ns: int
    """When it set off, on the rig's clock."""
    to: float | None = None
    """Where the setpoint is heading, in the channel's unit."""
    end_time_ns: int | None = None
    """When it gets there, on the rig's clock."""
    rate: float = 0.0
    """How fast the setpoint is moving now, per second."""


class SetPointGenerator:
    """A reference trajectory. Registered by tag when subclassed.

    `Sub.config` is the model that builds one from the wire; `sub.config`
    its values. `start` records where and when it set off (seconds from
    the clock's origin); `destination` and `landing` are what a subclass
    knows of where it is going once started, and either may be None.
    """

    tag: ClassVar[str] = ""
    config: ClassVar[Any] = None
    signal: Signal
    start_time: float = 0.0
    start_value: float = 0.0

    def __init_subclass__(
        cls, tag: str | None = None, register: bool = True, **kwargs: Any
    ) -> None:
        super().__init_subclass__(**kwargs)
        cls.tag = tag or cls.__dict__.get("tag") or to_snake(cls.__name__)
        if "config" not in cls.__dict__:
            model = creation_model(
                cls,
                suffix="Config",
                base=GeneratorConfig,
                extra={"tag": (Literal[cls.tag], cls.tag)},
            )
            model.generator = cls  # pyright: ignore[reportAttributeAccessIssue]
            model.init_names = tuple(signature(cls).parameters)  # pyright: ignore[reportAttributeAccessIssue]
            cls.config = ModelOf(model, tuple(model.model_fields))
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
        """Serialise as `{"tag": ...}`: a view of a running trajectory, not a way to build one."""
        return core_schema.json_or_python_schema(
            json_schema=core_schema.no_info_plain_validator_function(cls._reject),
            python_schema=core_schema.is_instance_schema(cls),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda g: {"tag": g.tag}, when_used="always"
            ),
        )

    @staticmethod
    def _reject(value: Any) -> Any:
        raise ValueError("a running trajectory cannot be built from the wire; send its config")

    def start(self, time: float, value: float) -> None:
        """Bind to the rig: `time` is the origin, `value` the setpoint then. Subclasses call up."""
        self.start_time = time
        self.start_value = value

    def generate(self, time: float) -> float:
        """The set point at `time`."""
        raise NotImplementedError

    def rate(self, time: float) -> float:
        """How fast the set point is moving at `time`, per second.

        Zero unless overridden: a generator with no notion of a rate (or one
        that has landed) is not moving. A rate feedforward uses this rather
        than differencing successive `generate()` values, which would carry
        the reading noise a real trajectory does not have.
        """
        return 0.0

    def destination(self) -> float | None:
        """Where the set point is heading; None for a trajectory with no destination."""
        return None

    def landing(self) -> float | None:
        """When it gets there, seconds from the clock's origin; None if it never does."""
        return None

    def trajectory(self, time: float, origin_ns: int) -> Trajectory:
        """The view at `time`; `origin_ns` is the clock's origin, to put the times on the wire."""
        landing = self.landing()
        return Trajectory(
            tag=self.tag,
            start=self.start_value,
            start_time_ns=origin_ns + round(self.start_time * 1e9),
            to=self.destination(),
            end_time_ns=None if landing is None else origin_ns + round(landing * 1e9),
            rate=self.rate(time),
        )


class LinearRampSetpoint(SetPointGenerator, tag="ramp"):
    """A set point walking to `to` at `pace`: a rate, or how long the whole ramp takes."""

    signal: Signal
    pace: Speed | Duration
    to: float
    end_time: float
    per_second: float

    def __init__(self, pace: Speed | Duration, to: float) -> None:
        self.pace = pace
        self.to = to
        self.signal = Signal()

    def start(self, time: float, value: float) -> None:
        super().start(time, value)
        span = self.to - value
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
            return self.to
        return self.to - (self.end_time - time) * self.per_second

    def rate(self, time: float) -> float:
        return 0.0 if time >= self.end_time else self.per_second

    def destination(self) -> float:
        return self.to

    def landing(self) -> float:
        return self.end_time
