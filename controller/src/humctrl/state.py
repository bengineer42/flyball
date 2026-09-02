from dataclasses import dataclass
from typing import Self

from humctrl.clock import Time
from humctrl.controller import ControllerState
from humctrl.controller.types import (
    ClosedControllerState,
    ClosedControllerView,
    ControlLawConfig,
    ControllerView,
)
from humctrl.pumps import PumpsOutput
from humctrl.pumps.types import PumpsSpec, PumpsView
from humctrl.readers import Readings
from humctrl.typing import Percent, Positive


@dataclass(slots=True, frozen=True)
class State:
    running: bool
    duration_ns: int
    pumps: PumpsOutput | None = None
    readings: Readings | None = None
    expected_humidity: Percent | None = None
    controller: ControllerState | None = None
    recording: bool | None = None


@dataclass(slots=True, frozen=True)
class Spec:
    start: Time
    process_time: Positive
    pumps: PumpsSpec | None = None
    default_control_law: ControlLawConfig | None = None


@dataclass(slots=True, frozen=True)
class View:
    start: Time
    duration_ns: int
    running: bool
    process_time: Positive
    pumps: PumpsView | None = None
    readings: Readings | None = None
    default_control_law: ControlLawConfig | None = None
    controller: ControllerView | None = None
    expected_humidity: Percent | None = None
    recording: bool | None = None

    @classmethod
    def from_spec_state(
        cls, spec: Spec, state: State, controller_config: ControlLawConfig | str | None = None
    ) -> Self:
        controller = None
        if isinstance(state.controller, ClosedControllerState) and isinstance(
            controller_config, ControlLawConfig
        ):
            controller = ClosedControllerView.from_spec_state(controller_config, state.controller)
        elif isinstance(state.controller, ControllerState) and controller_config is not None:
            controller = ControllerView.from_spec_state(controller_config, state.controller)

        return cls(
            start=spec.start,
            duration_ns=state.duration_ns,
            running=state.running,
            process_time=spec.process_time,
            pumps=PumpsView.from_spec_output(spec.pumps, state.pumps)
            if spec.pumps is not None and state.pumps is not None
            else None,
            readings=state.readings,
            default_control_law=spec.default_control_law,
            controller=controller,
            expected_humidity=state.expected_humidity,
            recording=state.recording,
        )
