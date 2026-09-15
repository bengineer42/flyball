"""What every device route shares: argument models, the schema, running a command."""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from flyball.core.device import CommandSpec, Device
from flyball.core.errors import NotFoundError
from flyball.core.reading import Measurand, Source
from flyball.server.wire import ArgumentsBase, arguments_model

_ARGUMENTS: dict[tuple[type[Device], str], type[ArgumentsBase]] = {}


def arguments_for(device_type: type[Device], spec: CommandSpec) -> type[ArgumentsBase]:
    """The request model for one device command, built once per class and tag."""
    key = (device_type, spec.tag)
    if key not in _ARGUMENTS:
        _ARGUMENTS[key] = arguments_model(
            spec.method, f"{device_type.__name__}{spec.tag.title().replace('_', '')}Arguments"
        )
    return _ARGUMENTS[key]


def command_for(device: Device, tag: str) -> CommandSpec:
    try:
        return type(device).commands[tag]
    except KeyError as e:
        raise NotFoundError(f"{device.name!r} has no command {tag!r}") from e


def measurand_schema(measurand: Measurand) -> dict[str, Any]:
    """A measurand for a gauge or an axis: unit, dimension, range, precision."""
    return {
        "label": measurand.label,
        "unit": measurand.unit.symbol,
        "dimension": measurand.unit.dimension.label,
        "range": measurand.range,
        "precision": measurand.precision,
    }


def source_schema(source: Source) -> dict[str, Any]:
    return {
        "name": str(source.name),
        "measurands": {ch.measurand.name: measurand_schema(ch.measurand) for ch in source.channels},
    }


def actuator_schema(actuator: Any) -> dict[str, Any]:
    """An actuator's device schema, with its demand unit on every field that is a demand.

    The base `demand` command and `state.demand` are declared on the generic
    `Actuator`, so their schemas cannot name a unit; the instance's
    `demand_unit` (a class attribute, or given by a rig file) is written onto
    them here, so a form or a readout shows it beside the number.
    """
    unit = actuator.demand_unit  # the instance may narrow the class
    symbol = None if unit is None else unit.symbol
    schema = device_schema(actuator, demand_unit=symbol)
    if symbol is not None:
        stamp = {"unit": symbol, "dimension": unit.dimension.label}
        for field in _nullable_field(schema["state"], "demand"):
            field.update(stamp)
        if (demand := schema["commands"].get("demand")) is not None:
            for field in _nullable_field(demand["arguments"], "demand"):
                field.update(stamp)
    return schema


def _nullable_field(schema: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """The number branches of a property, whether it is `number` or `anyOf [number, null]`."""
    prop = schema.get("properties", {}).get(name)
    if prop is None:
        return []
    branches = prop.get("anyOf") or [prop]
    return [b for b in branches if b.get("type") == "number"]


def device_schema(device: Device, **extra: Any) -> dict[str, Any]:
    """Config, settings, state and every command's request as JSON schema."""
    cls = type(device)
    return {
        "name": device.name,
        "type": cls.__name__,
        "description": cls.__doc__.strip().splitlines()[0] if cls.__doc__ else None,
        **extra,
        "config": TypeAdapter(cls.config_type).json_schema(mode="validation"),
        "settings": TypeAdapter(cls.settings_type).json_schema(mode="validation"),
        "state": TypeAdapter(cls.state_type).json_schema(mode="serialization"),
        "commands": {
            tag: {
                "description": spec.doc,
                "arguments": TypeAdapter(arguments_for(cls, spec)).json_schema(mode="validation"),
                "simulation": spec.simulation,
            }
            for tag, spec in cls.commands.items()
        },
    }


def run(device: Device, tag: str, body: dict[str, Any] | None) -> Any:
    """Call the marked method with the validated body; return whatever it returns."""
    spec = command_for(device, tag)
    arguments = arguments_for(type(device), spec).model_validate(body or {}).arguments()
    return spec.method(device, **arguments)
