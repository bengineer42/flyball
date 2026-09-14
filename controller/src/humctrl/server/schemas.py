"""Wire format.

Deliberately separate from the domain types in :mod:`humctrl.controller`,
:mod:`humctrl.pumps` and :mod:`humctrl.state`: the HTTP surface should be free
to change shape without dragging the control code with it, and vice versa.

Several domain types cannot cross the wire as they stand. ``Time`` has no
pydantic schema, ``Reading`` carries nanoseconds where a browser wants float
seconds, and the three ``BlendFlow`` variants are structurally identical so a
bare union of them cannot be told apart on the way back in. The models here
carry an explicit discriminator and do the conversion in one place.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Union

from pydantic import BaseModel, Field, TypeAdapter

from humctrl.core.clock import Duration, Rate, TimeUnit
from humctrl.control import ControlLawConfig, ControlLaws
from humctrl.pumps import Absolute, BlendFlow, OfBlendMax, OfGuaranteedMax, OnOverdrive
from humctrl.core.typing import NonNegative, Normalised, Percent


def generate_command_schema[T](
    methods: dict[str, type[T]],
    discriminator: str,
    parser: Callable[[type[T]], type[Any]] = lambda x: x,
) -> Any:
    return Annotated[
        Union[tuple(parser(command) for command in methods.values())],  # noqa: UP007
        Field(discriminator=discriminator),
    ]


class AbsoluteRequest(BaseModel):
    absolute: NonNegative
    on_overdrive: OnOverdrive = OnOverdrive.CLAMP

    def parse(self) -> Absolute:
        return Absolute(self.absolute, self.on_overdrive)


class OfBlendMaxRequest(BaseModel):
    of_blend_max: Normalised

    def parse(self) -> OfBlendMax:
        return OfBlendMax(self.of_blend_max)


class OfFullRangeMaxRequest(BaseModel):
    of_full_range_max: Normalised

    def parse(self) -> OfGuaranteedMax:
        return OfGuaranteedMax(self.of_full_range_max)


type BlendFlowRequest = AbsoluteRequest | OfBlendMaxRequest | OfFullRangeMaxRequest


# region Requests


class StartControllerRequest(BaseModel):
    """Start regulating. Omit ``control_law`` for open loop."""

    humidity: Percent
    flow: BlendFlowRequest | None = None
    #: A stored tuning by name, or a law config inline. Validated against the
    #: law its tag names, so a wrong gain is a 422 rather than a later failure.
    tuning: str | LawConfig | None = None

    def parse_tuning(self) -> ControlLawConfig | str | None:
        return self.tuning

    def parse_flow(self) -> BlendFlow | None:
        return self.flow and self.flow.parse()

    def parse(self) -> tuple[Percent, BlendFlow | None, ControlLawConfig | str | None]:
        return (self.humidity, self.parse_flow(), self.parse_tuning())


class SetPointRequest(BaseModel):
    humidity: Percent


class BlendRequest(BaseModel):
    """Total flow and blend ratio together."""

    flow: BlendFlowRequest
    wet_fraction: Normalised


class FlowsRequest(BaseModel):
    """Each line independently, in absolute flow units."""

    wet: NonNegative
    dry: NonNegative


class EffortsRequest(BaseModel):
    """Each line independently, in normalised effort units."""

    wet: Normalised
    dry: Normalised


LawConfig = generate_command_schema(ControlLaws, "tag", lambda law: law.config)
LawsSchema = TypeAdapter(LawConfig).json_schema()


class DefaultTuningRequest(BaseModel):
    tag: str


class DurationRequest(BaseModel):
    seconds: int
    nanoseconds: int

    def parse(self) -> Duration:
        return Duration(self.seconds, self.nanoseconds)


class RateRequest(BaseModel):
    per: TimeUnit
    value: float

    def parse(self) -> Rate:
        return Rate(self.value, self.per)
