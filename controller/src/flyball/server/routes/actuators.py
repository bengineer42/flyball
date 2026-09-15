"""Actuators: what each one is, and the commands its class marked.

Routes are resolved at request time against the live rig rather than mounted
per actuator, since the app exists before the rig is set. The price is that
OpenAPI lists one generic command route; ``/{name}/schema`` carries the real
request schema for each command, which is what a form or CLI reads anyway.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body
from pydantic import TypeAdapter

from flyball.core.errors import NotFoundError
from flyball.core.sink import Actuator, CommandSpec
from flyball.server.deps import RigDep
from flyball.server.wire import ArgumentsBase, arguments_model

router = APIRouter(prefix="/api/actuators", tags=["actuators"])

_ARGUMENTS: dict[tuple[type[Actuator[Any, Any]], str], type[ArgumentsBase]] = {}


def arguments_for(
    actuator_type: type[Actuator[Any, Any]], spec: CommandSpec
) -> type[ArgumentsBase]:
    """The request model for one actuator command, built once per class and tag."""
    key = (actuator_type, spec.tag)
    if key not in _ARGUMENTS:
        _ARGUMENTS[key] = arguments_model(
            spec.method, f"{actuator_type.__name__}{spec.tag.title().replace('_', '')}Arguments"
        )
    return _ARGUMENTS[key]


def _actuator(rig: RigDep, name: str) -> Actuator[Any, Any]:
    try:
        return rig.actuators[name]
    except KeyError as e:
        raise NotFoundError(f"Actuator {name!r} not found") from e


def _command(actuator: Actuator[Any, Any], tag: str) -> CommandSpec:
    try:
        return type(actuator).commands[tag]
    except KeyError as e:
        raise NotFoundError(f"Actuator {actuator.name!r} has no command {tag!r}") from e


def actuator_schema(actuator: Actuator[Any, Any]) -> dict[str, Any]:
    """Config, state and every command's request as JSON schema."""
    cls = type(actuator)
    return {
        "name": actuator.name,
        "type": cls.__name__,
        "demand_unit": cls.demand_unit.symbol if cls.demand_unit else None,
        "config": TypeAdapter(cls.config_type).json_schema(mode="validation"),
        "state": TypeAdapter(cls.state_type).json_schema(mode="serialization"),
        "commands": {
            tag: {
                "description": spec.doc,
                "arguments": TypeAdapter(arguments_for(cls, spec)).json_schema(mode="validation"),
            }
            for tag, spec in cls.commands.items()
        },
    }


@router.get("")
def read_actuators(rig: RigDep) -> dict[str, Any]:
    """Every attached actuator's view, by name."""
    return {name: actuator.view for name, actuator in rig.actuators.items()}


@router.get("/{name}")
def read_actuator(rig: RigDep, name: str) -> Any:
    return _actuator(rig, name).view


@router.get("/{name}/schema")
def read_actuator_schema(rig: RigDep, name: str) -> dict[str, Any]:
    return actuator_schema(_actuator(rig, name))


# Plain ``def``: FastAPI runs it in the threadpool, so a command that touches
# hardware never blocks the event loop.
@router.post("/{name}/{command}")
def run_command(
    rig: RigDep, name: str, command: str, body: Annotated[dict[str, Any] | None, Body()] = None
) -> Any:
    """Call the marked method with the validated body; respond with whatever it returns."""
    actuator = _actuator(rig, name)
    spec = _command(actuator, command)
    arguments = arguments_for(type(actuator), spec).model_validate(body or {}).arguments()
    result = spec.method(actuator, **arguments)
    actuator.apply()
    return result
