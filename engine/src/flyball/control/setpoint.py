from __future__ import annotations

from inspect import signature
from typing import Annotated, Any, ClassVar, Literal, Union

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_snake
from pydantic_core import core_schema

from flyball.foundation import Duration, Rate, Speed
from flyball.foundation.config import ModelOf, creation_model

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
    end_time: float | None = None
    """When the trajectory lands, in the time `start` was given; None until started, or endless."""

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

    @property
    def bounded(self) -> bool:
        """Whether the trajectory ends of its own accord, whatever it starts from."""
        return True

    def start(self, time: float, value: float) -> None:
        """Bind to the rig: `time` is the origin, `value` the process value then."""

    def generate(self, time: float) -> float:
        """The set point at `time`."""
        raise NotImplementedError

    def finished(self, time: float) -> bool:
        """Whether the trajectory has landed by `time` and nothing further will change.

        In the controller's time, as `generate` and `rate` take it, so a
        scaled or stepped clock is judged where it stands rather than by a
        wall-clock timer per trajectory.
        """
        return False

    def rate(self, time: float) -> float:
        """How fast the set point is moving at `time`, per second.

        Zero unless overridden: a generator with no notion of a rate (or one
        that has landed) is not moving. A rate feedforward uses this rather
        than differencing successive `generate()` values, which would carry
        the reading noise a real trajectory does not have.
        """
        return 0.0


class LinearRampSetpoint(SetPointGenerator):
    """A set point walking from where it starts to `end` at `pace`.

    `pace` is a speed (`per_minute: 10`) or how long the whole walk should
    take; either way the ramp starts at the value it is started from, so a
    ramp to where the set point already is lands at once.
    """

    view_fields: ClassVar[tuple[str, ...]] = ("end_time",)

    pace: Speed | Duration
    end: float
    end_time: float
    per_second: float

    def __init__(self, pace: Speed | Duration, end: float) -> None:
        self.pace = pace
        self.end = end

    def start(self, time: float, value: float) -> None:
        span = self.end - value
        duration = (
            abs(span / self.pace.per_second) if isinstance(self.pace, Rate) else self.pace.seconds
        )
        self.end_time = time + duration  # pyright: ignore[reportIncompatibleVariableOverride]
        # Signed by the distance: the pace says how fast, never which way, so a
        # descending ramp needs the sign taken from the span.
        self.per_second = span / duration if duration > 0.0 else 0.0

    def generate(self, time: float) -> float:
        if time >= self.end_time:
            return self.end
        return self.end - (self.end_time - time) * self.per_second

    def rate(self, time: float) -> float:
        return 0.0 if time >= self.end_time else self.per_second

    def finished(self, time: float) -> bool:
        return time >= self.end_time


class Hold(SetPointGenerator):
    """A fixed set point, as a trajectory: a profile's soak, or a plain setpoint with an end.

    With no `duration` it never finishes -- a runner decides when it has
    waited long enough, not the generator.
    """

    view_fields: ClassVar[tuple[str, ...]] = ("end_time",)

    value: float
    duration: Duration | None

    def __init__(self, value: float, duration: Duration | None = None) -> None:
        self.value = value
        self.duration = duration

    @property
    def bounded(self) -> bool:
        return self.duration is not None

    def start(self, time: float, value: float) -> None:
        self.end_time = None if self.duration is None else time + self.duration.seconds

    def generate(self, time: float) -> float:
        return self.value

    def finished(self, time: float) -> bool:
        return self.end_time is not None and time >= self.end_time


class ProfileConfig(SetPointGeneratorConfig):
    """A profile's segments are generator configs, so the union below refers to itself.

    Written out rather than derived from `__init__`, since `GeneratorConfig`
    does not exist until every generator is registered; `model_rebuild` at
    the end of the module closes the loop.
    """

    tag: Literal["profile"] = "profile"  # pyright: ignore[reportIncompatibleVariableOverride]
    segments: list[GeneratorConfig] = Field(min_length=1)  # type: ignore[valid-type]
    init_names: ClassVar[tuple[str, ...]] = ("segments",)


class Profile(SetPointGenerator):
    """Segments run back to back: each starts where the previous one landed.

    A ramp lands at its `end`, a hold at its `value`, a nested profile
    wherever its last segment does; the first segment starts from the value
    the profile is started at. A segment with no end (a hold without a
    duration) can only be last, since nothing after it would ever begin.

    Raises:
        ValueError: A segment other than the last never finishes.
    """

    config: ClassVar[Any] = ModelOf(ProfileConfig, ("segments",))
    view_fields: ClassVar[tuple[str, ...]] = ("active", "end_time")

    segments: list[SetPointGeneratorConfig]
    generators: list[SetPointGenerator]
    active: int | None
    """The index of the segment last asked for; None until the profile has been read."""

    def __init__(self, segments: list[SetPointGeneratorConfig]) -> None:
        self.segments = list(segments)
        if not self.segments:
            raise ValueError("a profile needs at least one segment")
        self.generators = [segment.build() for segment in self.segments]
        self.active = None
        for index, generator in enumerate(self.generators[:-1]):
            if not generator.bounded:
                raise ValueError(
                    f"profile segment {index} ({generator.tag}) never ends, so segment"
                    f" {index + 1} would never start; only the last segment may be endless"
                )

    @property
    def bounded(self) -> bool:
        return self.generators[-1].bounded

    def start(self, time: float, value: float) -> None:
        for generator in self.generators:
            generator.start(time, value)
            if generator.end_time is None:
                break
            time, value = generator.end_time, generator.generate(generator.end_time)
        self.end_time = self.generators[-1].end_time

    def _at(self, time: float) -> SetPointGenerator:
        """The segment in force at `time`: the first not yet finished, else the last."""
        last = len(self.generators) - 1
        self.active = next(
            (i for i, generator in enumerate(self.generators) if not generator.finished(time)),
            last,
        )
        return self.generators[self.active]

    def generate(self, time: float) -> float:
        return self._at(time).generate(time)

    def rate(self, time: float) -> float:
        return self._at(time).rate(time)

    def finished(self, time: float) -> bool:
        return self.generators[-1].finished(time)


ProfileConfig.generator = Profile

GeneratorConfig = Annotated[  # type: ignore[valid-type]
    Union[tuple(g.config for g in SetPointGenerators.values())],  # ruff: ignore[non-pep604-annotation-union]
    Field(discriminator="tag"),
]
"""Every built-in generator's config, discriminated by `tag`; a profile's segments are these."""

# A profile holds generators, so its config refers back to the union above:
# the forward reference can only be resolved now the union exists.
ProfileConfig.model_rebuild(_types_namespace={"GeneratorConfig": GeneratorConfig})
