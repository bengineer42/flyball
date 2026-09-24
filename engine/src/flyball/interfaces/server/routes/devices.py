"""Devices: the tree with live values, the schema, commands, and demands.

Every device in a rig has one name rig-wide, whatever its driver; this is the
one list. Routes resolve against the live rig at request time, since the app
exists before the rig is set. OpenAPI therefore lists one generic command
route; `/{name}/schema` carries each command's real request schema.

A write is the other side: `PUT /api/devices/{name}/write` puts values on
W signals under the device as one write, and `PUT /api/signals/{address}`
is the single-signal shorthand. Both answer with the write states by
address, and refuse -- 409 -- what the rig refuses: a signal a controller
drives, a signal that is not writable.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body
from pydantic import TypeAdapter, create_model

from flyball.foundation.device import CommandSpec, Device, Node, Signal
from flyball.foundation.errors import ConflictError, NotFoundError
from flyball.interfaces.server.deps import RigDep
from flyball.interfaces.server.schemas import DeviceOut, WriteOut, writes_out
from flyball.interfaces.server.wire import ArgumentsBase, wire_fields
from flyball.rig import DeviceRun, Rig

_ARGUMENTS: dict[tuple[type[Device], str], type[ArgumentsBase]] = {}

router = APIRouter(prefix="/api", tags=["devices"])


def arguments_for(device_type: type[Device], spec: CommandSpec) -> type[ArgumentsBase]:
    """The request model for one device command, built once per class and command.

    An argument that is a value for a demand may be left out: the rig fills
    it from the demand's current value, so the request does not require it.
    """
    key = (device_type, spec.name)
    if key not in _ARGUMENTS:
        fields = wire_fields(spec.method, skip=1)
        for name, param in spec.params.items():
            if param.link is not None and name in fields:
                # Its own type, not required: pydantic leaves a default alone, so
                # the schema keeps the constraints and a left-out one arrives None.
                annotation, default = fields[name]
                fields[name] = (annotation, None if default is ... else default)
        _ARGUMENTS[key] = create_model(
            f"{device_type.__name__}{spec.name.title().replace('_', '')}Arguments",
            __base__=ArgumentsBase,
            **fields,
        )
    return _ARGUMENTS[key]


def command_for(device: Device, command: str) -> CommandSpec:
    try:
        return device.commands[command]
    except KeyError as e:
        raise NotFoundError(f"{device.name!r} has no command {command!r}") from e


def _signal_schema(signal: Signal) -> dict[str, Any]:
    """A signal for a gauge, an axis or a target entry: unit, dimension, range, limits, type."""
    return {
        "address": signal.address,
        "access": str(signal.access),
        "role": signal.role.value,
        "tags": signal.tags,
        "label": signal.label,
        "quantity": signal.quantity.name,
        "unit": signal.unit.symbol,
        "dimension": signal.unit.dimension.label,
        "dtype": signal.spec.dtype,
        "value": TypeAdapter(signal.spec.vtype).json_schema(mode="serialization"),
        "range": signal.range,
        "precision": signal.spec.precision,
        "limits": signal.limits,
    }


def device_schema(device: Device, **extra: Any) -> dict[str, Any]:
    """Config, every signal, every input and every command's request as JSON schema."""
    cls = type(device)
    return {
        "name": device.name,
        "label": device.label,
        "class_name": cls.__name__,
        "driver": type(device.config).type_name,
        "description": cls.__doc__.strip().splitlines()[0] if cls.__doc__ else None,
        "readable": cls.readable,
        "writable": cls.writable,
        **extra,
        "config": TypeAdapter(cls.config_type).json_schema(mode="validation"),
        "signals": {path: _signal_schema(s) for path, s in device.signals.items()},
        "inputs": {
            role: {
                "label": spec.label,
                "quantity": spec.quantity.name,
                "unit": spec.quantity.unit.symbol,
                "bound": None if (b := device.bound.get(role)) is None else b.address,
            }
            for role, spec in cls.INPUTS.items()
        },
        "commands": {
            command: {
                "description": spec.doc,
                "arguments": _linking_demands(
                    _naming_signals(
                        TypeAdapter(arguments_for(cls, spec)).json_schema(mode="validation"),
                        device,
                    ),
                    spec,
                    device,
                ),
                "simulation": spec.simulation,
                "commit": spec.commit,
                "mode": spec.mode,
                "interrupts": spec.interrupts,
                "demand_of": spec.demand_of,
            }
            for command, spec in device.commands.items()
        },
    }


def _linking_demands(
    arguments: dict[str, Any], spec: CommandSpec, device: Device
) -> dict[str, Any]:
    """Each argument that is a value for a demand: its address, unit and effective limits.

    A form prefills it from the readback, shows the unit, and bounds the
    entry; the rig fills a missing one from the current value, so none is
    required.
    """
    properties = dict(arguments.get("properties", {}))
    required = list(arguments.get("required", []))
    for name, param in spec.params.items():
        if param.link is None or name not in properties:
            continue
        signal = device.signals[param.link]
        field = {
            **properties[name],
            "x-signal": signal.address,
            "unit": signal.unit.symbol,
            "title": signal.label or properties[name].get("title", name),
        }
        if (limits := signal.limits) is not None:
            field["minimum"], field["maximum"] = limits
        properties[name] = field
        if name in required:
            required.remove(name)
    out = {**arguments, "properties": properties}
    if "required" in arguments:
        out["required"] = required
    return out


def _naming_signals(arguments: dict[str, Any], device: Device) -> dict[str, Any]:
    """Narrow a string argument called `signal` to the device's own signal paths.

    The convention every driver follows (`fail(signal)`, `disturb(signal, offset)`):
    an argument by that name picks one of the device's signals, so a form can
    offer them rather than ask for free text.
    """
    field = arguments.get("properties", {}).get("signal")
    if field is None or field.get("type") != "string" or not device.signals:
        return arguments
    return {
        **arguments,
        "properties": {
            **arguments["properties"],
            "signal": {**field, "enum": list(device.signals)},
        },
    }


def run(rig: Rig, device: Device, command: str, body: dict[str, Any] | None) -> Any:
    """Run the command with the validated body, through the rig; return whatever it returns."""
    spec = command_for(device, command)
    arguments = arguments_for(type(device), spec).model_validate(body or {}).arguments()
    left_out = [n for n, p in spec.params.items() if p.link is not None and arguments[n] is None]
    for name in left_out:
        del arguments[name]  # the rig fills it from the demand's current value
    return rig.run_command(device, command, arguments)


def device_of(rig: Rig, name: str) -> Device:
    try:
        return rig.devices[name]
    except KeyError as e:
        raise NotFoundError(f"Device {name!r} not found") from e


def link_name(rig: Rig, device: Device) -> str | None:
    """The rig file's name for `device`'s link, if it was built on one.

    A driver keeps the built link under whatever attribute suits it (`link`,
    `plant`), so the device is searched for the object itself; the config's
    `link` is the built object too, unless the driver blanked it.
    """
    held = [*vars(device).values(), getattr(device.config, "link", None)]
    return next((name for name, built in rig.links.items() if any(h is built for h in held)), None)


def run_of(rig: Rig, name: str) -> DeviceRun | None:
    """How the runtime is polling `name`; None when nothing on it is polled."""
    return rig.polling.run(name) if name in rig.polling.by_name else None


def device_out(rig: Rig, device: Device) -> DeviceOut:
    with rig.lock:
        return DeviceOut.of(
            device,
            kind=rig.kind_of(device.name) or "device",
            latest=rig.latest,
            link=link_name(rig, device),
            run=run_of(rig, device.name),
            conditions=device.held_conditions(),
            last_usable=rig.router.last_usable,
            stale_after=rig.liveness.threshold_s,
        )


@router.get("/devices")
def read_devices(rig: RigDep) -> list[DeviceOut]:
    """Every device: its tree with the latest values and write states, commands, state."""
    devices = list(rig.devices.values())  # a snapshot: a device may be added meanwhile
    return [device_out(rig, device) for device in devices]


@router.get("/devices/{name}")
def read_device(rig: RigDep, name: str) -> DeviceOut:
    return device_out(rig, device_of(rig, name))


@router.get("/devices/{name}/schema")
def read_device_schema(rig: RigDep, name: str) -> dict[str, Any]:
    return device_schema(device_of(rig, name))


@router.post("/devices/{name}/restart")
def restart_device(rig: RigDep, name: str) -> DeviceOut:
    """Poll an offline device again on its period, after whatever was wrong has been put right."""
    device = device_of(rig, name)
    rig.polling.restart(name)
    return device_out(rig, device)


@router.put("/devices/{name}/write")
def write(rig: RigDep, name: str, body: dict[str, float]) -> dict[str, WriteOut]:
    """Put values on W signals under the device, as one write; keys are relative names.

    Dotted for a signal under a namespace (`position.x`). Committed at
    once; the response is the write state of each signal set, by address.
    409 for a signal a controller drives,
    or a signal that is not writable; 404 for a name that is not under the
    device; 422 for a value that is not finite (NaN, infinity).
    """
    device = device_of(rig, name)
    values: dict[str | Signal, float] = {name: value for name, value in body.items()}
    return writes_out(rig.write(device.root, values))


@router.put("/signals/{address}")
def set_signal(rig: RigDep, address: str, body: Annotated[float, Body()]) -> dict[str, WriteOut]:
    """The single-signal demand: the body is the value, in the signal's unit."""
    target = rig.resolve(address)
    if isinstance(target, Node):
        raise ConflictError(f"'{address}' is a namespace, not a signal: demand on its device")
    return writes_out(rig.write(target.node, {target: body}))


# Plain `def`: FastAPI runs it in the threadpool, so a command that touches
# hardware never blocks the event loop.
@router.post("/devices/{name}/commands/{command}")
def run_command(
    rig: RigDep, name: str, command: str, body: Annotated[dict[str, Any] | None, Body()] = None
) -> Any:
    """Call the marked method with the validated body; respond with whatever it returns.

    A command that succeeds on an offline device is taken as the fix
    (`restore`, a reset, a reconnect): polling starts again, and a device
    still broken simply goes offline again with a fresh event.
    """
    device = device_of(rig, name)
    result = run(rig, device, command, body)
    rig.polling.revive(name)
    return result
