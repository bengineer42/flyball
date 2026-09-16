from __future__ import annotations

from inspect import signature
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_snake
from pydantic_core import core_schema

from flyball.core import Duration, Rate, Speed, Trigger
from flyball.core.model import ModelOf, creation_model

SetPointGenerators: dict[str, type[SetPointGenerator]] = {}
"""Every registered generator, keyed by the tag it crosses the wire under."""


class SetPointGeneratorConfig(BaseModel):
    """How a generator was specified: its constructor arguments and its tag.

    `tag` is declared on the base so the base has a schema; each subclass
    narrows it to a `Literal`, which lets a union of configs discriminate on
    it -- the same shape [ControlLawConfig][flyball.control.types.ControlLawConfig]
    gives laws.
    """

    model_config = ConfigDict(extra="forbid")

    generator: ClassVar[type[SetPointGenerator]]
    init_names: ClassVar[tuple[str, ...]] = ()

    tag: str

    def build(self) -> SetPointGenerator:
        """A fresh generator, not yet started."""
        return self.generator(**{name: getattr(self, name) for name in self.init_names})


class SetPointGenerator:
    """A reference trajectory. Registered by tag when subclassed."""

    tag: ClassVar[str] = ""
    config: ClassVar[Any] = None
    view_fields: ClassVar[tuple[str, ...]] = ()
    """Attributes beyond the constructor's that a running instance shows on the wire."""
    signal: Trigger

    def __init_subclass__(
        cls, tag: str | None = None, register: bool = True, **kwargs: Any
    ) -> None:
        super().__init_subclass__(**kwargs)
        cls.tag = tag or cls.__dict__.get("tag") or to_snake(cls.__name__)

        # Every generator gets its own config, derived from `__init__`: the
        # field list a form needs to build one, plus the tag that names it.
        if "config" not in cls.__dict__:
            config_model = creation_model(
                cls,
                suffix="Config",
                base=SetPointGeneratorConfig,
                extra={"tag": (Literal[cls.tag], cls.tag)},
            )
            config_model.generator = cls  # pyright: ignore[reportAttributeAccessIssue]
            config_model.init_names = tuple(signature(cls).parameters)  # pyright: ignore[reportAttributeAccessIssue]
            cls.config = ModelOf(config_model, tuple(config_model.model_fields))

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
        """Serialise as `config` plus any `view_fields`: a view, not a way to build one."""
        return core_schema.json_or_python_schema(
            json_schema=core_schema.no_info_plain_validator_function(cls._reject),
            python_schema=core_schema.is_instance_schema(cls),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda g: g.wire(), when_used="always"
            ),
        )

    @staticmethod
    def _reject(value: Any) -> Any:
        raise ValueError("a running trajectory cannot be built from the wire")

    def wire(self) -> dict[str, Any]:
        """`{"tag": ..., **the constructor's arguments, **view_fields present so far}`."""
        data = self.config.model_dump(mode="json")
        for name in type(self).view_fields:
            value = getattr(self, name, None)
            if value is not None:
                data[name] = value
        return data

    def start(self, time: float, value: float) -> None:
        """Bind to the rig: `time` is the origin, `value` the process value then."""

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


class LinearRampSetpoint(SetPointGenerator):
    """A set point walking from `start` to `end` between two instants."""

    view_fields: ClassVar[tuple[str, ...]] = ("end_time",)

    signal: Trigger
    pace: Speed | Duration
    end: float
    end_time: float
    per_second: float

    def __init__(self, pace: Speed | Duration, end: float) -> None:
        self.pace = pace
        self.end = end
        self.signal = Trigger()

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

    def rate(self, time: float) -> float:
        return 0.0 if time >= self.end_time else self.per_second
