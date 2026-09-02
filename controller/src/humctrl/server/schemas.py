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

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field

from humctrl.controller.laws import (
    ControlLawConfig,
    PControllerConfig,
    PIControllerConfig,
    PIDControllerConfig,
)
from humctrl.pumps import Absolute, BlendFlow, OfBlendMax, OfFullRangeMax, OnOverdrive
from humctrl.readers import Reading
from humctrl.typing import NonNegative, Normalised, Percent, Positive

# A control law, chosen by the same ``type`` tag used in config files.
LawConfig = Annotated[
    PControllerConfig | PIControllerConfig | PIDControllerConfig,
    Field(discriminator="type"),
]


class FlowScale(StrEnum):
    """How to read :attr:`FlowRequest.value`."""

    ABSOLUTE = "absolute"  # in the pumps' flow units, e.g. LPM
    BLEND_MAX = "blend_max"  # fraction of the most this blend can deliver
    FULL_RANGE_MAX = "full_range_max"  # fraction of the most any blend can deliver


class FlowRequest(BaseModel):
    """A total flow. ``scale`` disambiguates the three ``BlendFlow`` variants."""

    value: NonNegative = 1.0
    scale: FlowScale = FlowScale.FULL_RANGE_MAX
    on_overdrive: OnOverdrive | None = None

    def to_blend_flow(self) -> BlendFlow:
        match self.scale:
            case FlowScale.ABSOLUTE:
                if self.on_overdrive is None:
                    return Absolute(self.value)
                return Absolute(self.value, self.on_overdrive)
            case FlowScale.BLEND_MAX:
                return OfBlendMax(self.value)
            case FlowScale.FULL_RANGE_MAX:
                return OfFullRangeMax(self.value)


class FlowState(BaseModel):
    """The flow a controller is holding, as :class:`FlowRequest` would express it."""

    value: NonNegative
    scale: FlowScale

    @classmethod
    def of(cls, flow: BlendFlow) -> FlowState:
        match flow:
            case Absolute():
                return cls(value=flow.value, scale=FlowScale.ABSOLUTE)
            case OfBlendMax():
                return cls(value=flow.value, scale=FlowScale.BLEND_MAX)
            case OfFullRangeMax():
                return cls(value=flow.value, scale=FlowScale.FULL_RANGE_MAX)


class ReadingState(BaseModel):
    """A sensor reading. ``time`` is seconds, not the domain's nanoseconds."""

    time: float
    humidity: Percent
    temperature: float
    source: str | None = None

    @classmethod
    def of(cls, reading: Reading) -> ReadingState:
        return cls(
            time=reading.time,
            humidity=reading.humidity,
            temperature=reading.temperature,
            source=reading.source,
        )


class ReadingsState(BaseModel):
    """The three lines. A line that failed to read reports its error, not a value."""

    process: ReadingState | None = None
    dry: ReadingState | None = None
    wet: ReadingState | None = None
    errors: dict[str, str] = Field(default_factory=dict)


class Lines[T](BaseModel):
    wet: T
    dry: T


class PumpConfig(BaseModel):
    max_flows: Lines[Positive]
    units: str | None
    full_range_max_flow: Positive


class ControllerState(BaseModel):
    """What the controller is doing. ``demand`` may leave 0-100 when railed."""

    type: str
    set_point: Percent
    demand: float
    flow: FlowState
    suspended: bool


# region Requests


class StartControllerRequest(BaseModel):
    """Start regulating. Omit ``control_law`` for open loop."""

    humidity: Percent
    flow: FlowRequest | None = None
    control_law: LawConfig | None = None

    def law(self) -> ControlLawConfig | None:
        return self.control_law


class SetPointRequest(BaseModel):
    humidity: Percent


class BlendRequest(BaseModel):
    """Total flow and blend ratio together."""

    flow: FlowRequest
    wet_fraction: Normalised


class FlowsRequest(BaseModel):
    """Each line independently, in absolute flow units."""

    wet: NonNegative
    dry: NonNegative


class EffortsRequest(BaseModel):
    """Each line independently, in normalised effort units."""

    wet: Normalised
    dry: Normalised


class RecordingRequest(BaseModel):
    flag: str | None = None


# endregion
