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
            }
            for tag, spec in cls.commands.items()
        },
    }


def run(device: Device, tag: str, body: dict[str, Any] | None) -> Any:
    """Call the marked method with the validated body; return whatever it returns."""
    spec = command_for(device, tag)
    arguments = arguments_for(type(device), spec).model_validate(body or {}).arguments()
    return spec.method(device, **arguments)
