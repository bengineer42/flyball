from dataclasses import dataclass
from typing import Self

from humctrl.clock import Time
from humctrl.controller import (
    ControllerView,
    Tuning,
)
from humctrl.pumps import Efforts, PumpsOutput
from humctrl.pumps.types import Flows, PumpsSpec, PumpsView
from humctrl.readers import Readings
from humctrl.typing import Percent, Positive


@dataclass(slots=True, frozen=True)
class State:
    running: bool
    duration_ns: int
    pumps: PumpsOutput | None = None
    readings: Readings | None = None
    expected_humidity: Percent | None = None
    controller: ControllerView | None = None
    recording: bool | None = None


@dataclass(slots=True, frozen=True)
class Spec:
    start: Time
    process_time: Positive
    default_tuning: Tuning
    pumps: PumpsSpec | None = None


@dataclass(slots=True, frozen=True)
class View:
    start: Time
    duration_ns: int
    running: bool
    process_time: Positive
    pumps: PumpsView | None = None
    readings: Readings | None = None
    default_tuning: Tuning | None = None
    controller: ControllerView | None = None
    expected_humidity: Percent | None = None
    recording: bool | None = None

    @classmethod
    def of(cls, spec: Spec, state: State) -> Self:

        return cls(
            start=spec.start,
            duration_ns=state.duration_ns,
            running=state.running,
            process_time=spec.process_time,
            pumps=PumpsView.from_spec_output(spec.pumps, state.pumps)
            if spec.pumps is not None and state.pumps is not None
            else None,
            readings=state.readings,
            default_tuning=spec.default_tuning,
            controller=state.controller,
            expected_humidity=state.expected_humidity,
            recording=state.recording,
        )


@dataclass(frozen=True, slots=True)
class ControllerOutput(ControllerView):
    efforts: Efforts
    flows: Flows
    expected_humidity: Percent | None

    @classmethod
    def from_parts(
        cls,
        controller: ControllerView,
        pumps: PumpsOutput,
        expected_humidity: Percent | None,
    ) -> Self:
        return cls(
            set_point=controller.set_point,
            correction=controller.correction,
            flow=controller.flow,
            flow_humidities=controller.flow_humidities,
            suspended=controller.suspended,
            law=controller.law,
            flows=pumps.flows,
            efforts=pumps.efforts,
            expected_humidity=expected_humidity,
        )
