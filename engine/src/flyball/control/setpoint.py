from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import Field
from pydantic_core import core_schema

from flyball.foundation import Duration, Rate, Speed
from flyball.model.generator import (
    SetPointGenerator,
    SetPointGeneratorConfig,
    registered_generator_configs,
)
from flyball.model.model import ModelOf, discriminated_union


class _GeneratorConfigType:
    """`GeneratorConfig`'s own type: every registered generator's config, by `tag`.

    Resolved when a value is validated, not when this module is imported, so a
    generator registered afterwards (an extension's, loaded by `Catalogs.discover()`,
    or simply another module's own subclass) is recognised wherever this type is
    used: a profile's segments as much as a controller's reference -- both are
    `GeneratorConfig`, so both see the same registrations.
    """

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> core_schema.CoreSchema:
        # The union as registered right now, built into the schema the normal way (so a
        # profile nested in its own segments gets pydantic's usual `$ref`-based recursion,
        # and the JSON schema lists every tag known at this point). Wrapped so a tag
        # registered *after* this schema was built (an extension loaded later) still
        # resolves: `_validate_late` only runs when the frozen schema below would refuse it.
        known = registered_generator_configs()
        frozen = handler.generate_schema(discriminated_union(known, "tag"))

        def validate(value: Any, next_: core_schema.ValidatorFunctionWrapHandler) -> Any:
            tag = value.get("tag") if isinstance(value, dict) else getattr(value, "tag", None)
            return next_(value) if tag in known else cls._validate_late(value)

        return core_schema.no_info_wrap_validator_function(
            validate,
            frozen,
            # Dumped by its own tag/fields, not by re-matching the (possibly stale) union
            # above -- the value is already some `SetPointGeneratorConfig`, whichever one.
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda v: v.model_dump(mode="json"), when_used="always"
            ),
        )

    @staticmethod
    def _validate_late(value: Any) -> SetPointGeneratorConfig:
        """A generator registered after this schema was built: looked up live."""
        if isinstance(value, SetPointGeneratorConfig):
            return value
        if not isinstance(value, dict):
            raise ValueError(f"a generator config is an object with a 'tag', not {value!r}")
        configs = registered_generator_configs()
        tag = value.get("tag")
        config_cls = configs.get(tag) if isinstance(tag, str) else None
        if config_cls is None:
            raise ValueError(f"generator tag {tag!r} is not registered (known: {sorted(configs)})")
        return config_cls.model_validate(value)


GeneratorConfig = Annotated[Any, _GeneratorConfigType]
"""Every registered generator's config, discriminated by `tag`; a profile's segments are these,
looked up live rather than fixed to what `control/setpoint.py` itself defines."""


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
    """A profile's segments are generator configs -- `GeneratorConfig` itself, resolved live.

    Written out rather than derived from `__init__`, since `Profile`'s own
    constructor does not exist yet (`ProfileConfig.generator = Profile` below
    closes that loop, once `Profile` is defined).
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
