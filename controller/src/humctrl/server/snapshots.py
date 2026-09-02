"""Domain object -> wire model.

One place that knows how :mod:`humctrl.state` becomes :mod:`humctrl.server.schemas`,
so the routes stay free of conversion detail.
"""

from __future__ import annotations

from humctrl.manager import Manager
from humctrl.pumps import PumpsOutput
from humctrl.readers import Reading, Readings
from humctrl.server.schemas import (
    ControllerState,
    Flows,
    FlowState,
    ReadingsState,
    ReadingState,
)
from humctrl.state import State


def pumps_state(outputs: PumpsOutput | None) -> PumpsState | None:
    if outputs is None:
        return None
    return PumpsState(
        flows=Flows(wet=outputs.flows.wet, dry=outputs.flows.dry, units=outputs.flows.units),
        efforts=Efforts(wet=outputs.efforts.wet, dry=outputs.efforts.dry),
    )


def readings_state(readings: Readings) -> ReadingsState:
    """Split the three lines into values and errors.

    A line whose sensor raised carries the exception rather than a reading, so
    the client is told which line failed instead of quietly seeing nothing.
    """
    values: dict[str, ReadingState | None] = {}
    errors: dict[str, str] = {}
    for source, reading in readings:
        name = source.value
        if isinstance(reading, Reading):
            values[name] = ReadingState.of(reading)
        elif isinstance(reading, Exception):
            errors[name] = str(reading)
    return ReadingsState(**values, errors=errors)


def controller_state(manager: Manager) -> ControllerState | None:
    controller = manager.controller
    if controller is None:
        return None
    state = controller.state
    return ControllerState(
        type=state.type,
        set_point=state.set_point,
        demand=state.demand,
        flow=FlowState.of(state.flow),
        suspended=controller.suspended,
    )


def rig_state(manager: Manager) -> RigState:
    state: State = manager.state
    return RigState(
        time=state.time.seconds,
        running=manager.running,
        recording=manager.recording,
        pumps=pumps_state(state.pumps),
        controller=controller_state(manager),
        readings=readings_state(
            Readings(process=state.process_reading, dry=state.dry_reading, wet=state.wet_reading)
        ),
        flow_humidity=state.flow_humidity,
    )
