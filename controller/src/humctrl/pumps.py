from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from typing import Protocol

from humctrl.utils import format_quantity, validate_normalised


class PumpError(Exception):
    """Base for everything humctrl.pumps raises."""


class PumpErrorGroup(ExceptionGroup, PumpError): ...


class FlowError(PumpError, ValueError):
    """A requested flow or fraction cannot be applied."""


class FlowUnreachableError(FlowError):
    wet_flow: float
    dry_flow: float
    max_flow_at: float
    max_wet_flow: float
    max_dry_flow: float
    wet_fraction: float
    units: str | None

    def __init__(
        self,
        wet_flow: float,
        dry_flow: float,
        max_wet_flow: float,
        max_dry_flow: float,
        max_flow: float,
        wet_fraction: float | None = None,
        units: str | None = None,
    ):
        self.wet_flow = wet_flow
        self.dry_flow = dry_flow
        self.max_flow_at = max_flow
        self.max_wet_flow = max_wet_flow
        self.max_dry_flow = max_dry_flow
        self.wet_fraction = wet_fraction
        self.units = units
        string = "Flow unreachable."
        if not self.total_reachable:
            string += f"\n\rMax Flow {format_quantity(self.total_flow, units=units)} exceeds {format_quantity(max_flow, units=units)}"
            if wet_fraction is not None:
                string += f" at wet fraction {wet_fraction:.3f}"
            string += "."
        if not self.wet_reachable:
            string += f"\n\rWet flow {format_quantity(wet_flow, units=units)} exceeds {format_quantity(max_wet_flow, units=units)}."
        if not self.dry_reachable:
            string += f"\n\rDry flow {format_quantity(dry_flow, units=units)} exceeds {format_quantity(max_dry_flow, units=units)}."

        super().__init__(string)

    @cached_property
    def total_flow(self) -> float:
        return self.wet_flow + self.dry_flow

    @cached_property
    def wet_reachable(self) -> bool:
        return self.wet_flow <= self.max_wet_flow

    @cached_property
    def dry_reachable(self) -> bool:
        return self.dry_flow <= self.max_dry_flow

    @cached_property
    def total_reachable(self) -> bool:
        return self.total_flow <= self.max_flow_at


class PumpStateError(PumpError):
    """Operation invalid in the current mode."""


class PumpHardwareError(PumpError):
    """The underlying device failed."""


class FlowScale(Enum):
    """How to interpret a flow value."""

    ABSOLUTE = "absolute"  # in _flow_units, e.g. LPM
    FULL_RANGE_MAX = "full_range_max"  # fraction of max_full_range_flow
    BLEND_MAX = "blend_max"  # fraction of max_flow_at(wet_fraction)


@dataclass(frozen=True, slots=True)
class Flow:
    scale: FlowScale
    value: float

    @classmethod
    def full_range_max(cls, value: float = 1.0) -> Flow:
        return cls(FlowScale.FULL_RANGE_MAX, value)

    @classmethod
    def blend_max(cls, value: float = 1.0) -> Flow:
        return cls(FlowScale.BLEND_MAX, value)

    @classmethod
    def absolute(cls, value: float) -> Flow:
        return cls(FlowScale.ABSOLUTE, value)


class Pump(Protocol):

    @property
    def max_flow(self) -> float: ...

    def set_flow(self, flow: float): ...

    def stop(self): ...


@dataclass(frozen=True)
class AbsoluteFlows:
    wet: float
    dry: float
    units: str | None = None

    @cached_property
    def total(self) -> float:
        return self.wet + self.dry

    @cached_property
    def wet_fraction(self) -> float:
        return self.wet / self.total if self.wet > 0 else 0.0

    @classmethod
    def from_blend(
        cls, total: float, wet_fraction: float, units: str | None = None
    ) -> AbsoluteFlows:
        return cls(total * wet_fraction, total * (1.0 - wet_fraction), units=units)

    @classmethod
    def from_wet_total(
        cls, wet: float, total: float, units: str | None = None
    ) -> AbsoluteFlows:
        return cls(wet, total - wet, units=units)

    @classmethod
    def from_dry_total(
        cls, dry: float, total: float, units: str | None = None
    ) -> AbsoluteFlows:
        return cls(total - dry, dry, units=units)

    @classmethod
    def from_wet_fraction(
        cls, wet: float, wet_fraction: float, units: str | None = None
    ) -> AbsoluteFlows:
        validate_normalised("wet_fraction", wet_fraction)
        return cls.from_blend(wet / wet_fraction, wet_fraction, units=units)

    @classmethod
    def from_dry_fraction(
        cls, dry: float, wet_fraction: float, units: str | None = None
    ) -> AbsoluteFlows:
        validate_normalised("wet_fraction", wet_fraction)
        return cls.from_blend(dry / (1.0 - wet_fraction), wet_fraction, units=units)


class FractionChangePolicy(Enum):
    HOLD_OR_RAISE = "hold_or_raise"
    HOLD_CLAMPED = "hold_clamped"
    TRACKED_MAX = "tracked_max"

    @property
    def clamp(self) -> bool:
        return self == FractionChangePolicy.HOLD_CLAMPED


class RaiseOrClamp(Enum):
    """How to handle a requested flow change that exceeds the maximum."""

    RAISE = "raise"  # raise FlowUnreachableError
    CLAMP = "clamp"  # clamp to the maximum flow

    @property
    def clamp(self) -> bool:
        return self == RaiseOrClamp.CLAMP


class DualPumps(Protocol):
    wet_pump: Pump
    dry_pump: Pump
    _flow_units: str | None = None
    _wet_flow: float = 0.0
    _dry_flow: float = 0.0

    _wet_pump_running: bool = False
    _dry_pump_running: bool = False

    def __init__(
        self,
        wet_pump: Pump,
        dry_pump: Pump,
        flow_units: str | None = None,
    ):
        self.wet_pump = wet_pump
        self.dry_pump = dry_pump
        self._flow_units = flow_units

    def raise_flow_error(
        self,
        flow: float,
        wet_flow: float,
        dry_flow: float,
        max_flow: float,
        wet_fraction: float | None = None,
    ):
        raise FlowUnreachableError(
            flow=flow,
            wet_flow=wet_flow,
            dry_flow=dry_flow,
            max_flow=max_flow,
            max_wet_flow=self.max_wet_flow,
            max_dry_flow=self.max_dry_flow,
            wet_fraction=wet_fraction,
            units=self._flow_units,
        )

    @property
    def wet_pump_running(self) -> bool:
        return self._wet_pump_running

    @property
    def dry_pump_running(self) -> bool:
        return self._dry_pump_running

    @property
    def wet_flow(self) -> float:
        return self._wet_flow if self._wet_pump_running else 0.0

    @property
    def dry_flow(self) -> float:
        return self._dry_flow if self._dry_pump_running else 0.0

    @property
    def flows(self) -> AbsoluteFlows:
        return AbsoluteFlows(self.wet_flow, self.dry_flow, units=self._flow_units)

    @property
    def flow_units(self) -> str | None:
        return self._flow_units

    @property
    def total_flow(self) -> float:
        return self.wet_flow + self.dry_flow

    @property
    def max_wet_flow(self) -> float:
        return self.wet_pump.max_flow

    @property
    def max_dry_flow(self) -> float:
        return self.dry_pump.max_flow

    @cached_property
    def max_full_range_flow(self) -> float:
        return min(self.max_wet_flow, self.max_dry_flow)

    @cached_property
    def max_total_flow(self) -> float:
        return self.max_wet_flow + self.max_dry_flow

    @property
    def wet_fraction(self) -> float:
        return self.wet_flow / (self.total_flow)

    @property
    def dry_fraction(self) -> float:
        return self.dry_flow / (self.total_flow)

    def flow_str(self, flow: float) -> str:
        return format_quantity(flow, units=self._flow_units)

    def max_flow_at(self, wet_fraction: float) -> float:
        return self._max_flow_at(validate_normalised("wet_fraction", wet_fraction))

    def _max_flow_at(self, wet_fraction: float) -> float:
        wet_flow_limit = self.max_wet_flow / wet_fraction
        dry_flow_limit = self.max_dry_flow / (1.0 - wet_fraction)
        return min(wet_flow_limit, dry_flow_limit)

    def validate_blend(self, flow: float, wet_fraction: float) -> AbsoluteFlows:
        flows = AbsoluteFlows.from_blend(
            flow,
            validate_normalised("wet_fraction", wet_fraction),
            units=self._flow_units,
        )
        if (
            flows.wet > self.max_wet_flow
            or flows.dry > self.max_dry_flow
            or flows.total > self.max_total_flow
        ):
            raise FlowUnreachableError(
                flows=flows,
                max_wet_flow=self.max_wet_flow,
                max_dry_flow=self.max_dry_flow,
                max_flow=self.max_flow_at(wet_fraction),
            )
        return flows

    def validate_flows(self, wet_flow: float, dry_flow: float):
        if (
            wet_flow > self.max_wet_flow
            or dry_flow > self.max_dry_flow
            or (wet_flow + dry_flow) > self.max_total_flow
        ):
            raise FlowUnreachableError(
                wet_flow=wet_flow,
                dry_flow=dry_flow,
                max_flow=self.max_total_flow,
                max_wet_flow=self.max_wet_flow,
                max_dry_flow=self.max_dry_flow,
                units=self._flow_units,
            )

    def blend_to_flows(
        self, flow: Flow | float, wet_fraction: float, clamp: bool = False
    ) -> AbsoluteFlows:
        max_flow_at = self.max_flow_at(wet_fraction)
        if isinstance(flow, Flow):
            match flow.scale:
                case FlowScale.ABSOLUTE:
                    flow = flow.value
                case FlowScale.FULL_RANGE_MAX:
                    flow = flow.value * self.max_full_range_flow
                case FlowScale.BLEND_MAX:
                    flow = flow.value * max_flow_at
        flow = min(flow, max_flow_at) if clamp else flow
        return self.validate_blend(flow, wet_fraction)

    def set_flow(self, flow: Flow | float, clamp: bool = False) -> AbsoluteFlows:
        flows = self.blend_to_flows(flow, self.wet_fraction, clamp=clamp)
        self._set_flows(flows.wet, flows.dry)
        return flows

    def set_blend(
        self, flow: Flow | float, wet_fraction: float, clamp: bool = False
    ) -> AbsoluteFlows:
        flows = self.blend_to_flows(flow, wet_fraction, clamp=clamp)
        self._set_flows(flows.wet, flows.dry)
        return flows

    def set_wet_fraction(
        self,
        wet_fraction: float,
        policy: FractionChangePolicy = FractionChangePolicy.HOLD_OR_RAISE,
    ) -> AbsoluteFlows:
        flow = self.total_flow
        if policy == FractionChangePolicy.TRACKED_MAX:
            flow = Flow(
                FlowScale.BLEND_MAX,
                self.total_flow / self.max_flow_at(self.wet_fraction),
            )
        return self.set_blend(flow, wet_fraction, clamp=policy.clamp)

    def set_flows(self, wet_flow: float, dry_flow: float):
        self.validate_flows(wet_flow, dry_flow)
        self._set_flows(wet_flow, dry_flow)

    def _set_flows(self, wet_flow: float, dry_flow: float):
        self._set_wet_flow(wet_flow)
        self._set_dry_flow(dry_flow)

    def _set_wet_flow(self, flow: float):
        try:
            self.wet_pump.set_flow(flow)
            self._wet_flow = flow
            self._wet_pump_running = True
        except Exception as e:
            raise PumpHardwareError(
                f"Wet pump failed to set flow: {e.__class__.__name__}: {e}"
            ) from e

    def _set_dry_flow(self, flow: float):
        try:
            self.dry_pump.set_flow(flow)
            self._dry_flow = flow
            self._dry_pump_running = True
        except Exception as e:
            raise PumpHardwareError(
                f"Dry pump failed to set flow: {e.__class__.__name__}: {e}"
            ) from e

    def stop_wet(self):
        try:
            self.wet_pump.stop()
            self._wet_flow = 0.0
            self._wet_pump_running = False
        except Exception as e:
            raise PumpHardwareError(
                f"Wet pump failed to stop: {e.__class__.__name__}: {e}"
            ) from e

    def stop_dry(self):
        try:
            self.dry_pump.stop()
            self._dry_flow = 0.0
            self._dry_pump_running = False
        except Exception as e:
            raise PumpHardwareError(
                f"Dry pump failed to stop: {e.__class__.__name__}: {e}"
            ) from e

    def stop(self):
        errors: list[PumpHardwareError] = []
        for stop_one in (self.stop_wet, self.stop_dry):
            try:
                stop_one()
            except PumpHardwareError as e:
                errors.append(e)
        if errors:
            raise PumpErrorGroup("Failed to stop pumps", errors)
