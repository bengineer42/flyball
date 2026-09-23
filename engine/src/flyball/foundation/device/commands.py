"""`@command`: marking a device method runnable by people, programs and MCP tools."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, get_type_hints, overload

from pydantic import TypeAdapter
from pydantic.errors import (
    PydanticInvalidForJsonSchema,
    PydanticSchemaGenerationError,
    PydanticUndefinedAnnotation,
)
from pydantic.json_schema import JsonSchemaMode

RESERVED_NAMES = frozenset({"schema"})
"""Route segments the server uses after a device's name; no device or command may take them."""


def _schemable(owner: type, attr: str, model: Any, mode: JsonSchemaMode) -> None:
    """Fail at class definition if pydantic cannot describe `model`."""
    try:
        TypeAdapter(model).json_schema(mode=mode)
    except PydanticUndefinedAnnotation:
        pass  # a forward reference; resolved on first real use
    except (PydanticSchemaGenerationError, PydanticInvalidForJsonSchema) as e:
        shown = getattr(model, "__name__", repr(model))
        raise TypeError(f"{owner.__name__}.{attr} ({shown}) has no JSON schema: {e}") from e


def _check_command_signature(owner: type, spec: CommandSpec) -> None:
    """Every argument and the return of a command must cross the wire.

    Checked at class definition, not on the first request.
    """
    try:
        hints = get_type_hints(spec.method)
    except NameError:
        return  # a forward reference; the server's model derives it later
    for name, annotation in hints.items():
        where = (
            f"{spec.method.__name__}() -> "
            if name == "return"
            else f"{spec.method.__name__}({name})"
        )
        if annotation is type(None):
            continue
        _schemable(owner, where, annotation, "serialization" if name == "return" else "validation")


@dataclass(frozen=True, slots=True)
class Param:
    """One argument of a command: its type, and the demand it is a value for, if any."""

    name: str
    annotation: Any
    link: str | None = None
    """The path of the demand or setting this argument is a value for: from a descriptor in an
    `Annotated[...]` annotation, or a parameter named like a descriptor of the class. The rig
    fills a missing argument from its current value and clamps a demand's to its limits; the
    schema shows its unit, limits and address."""
    default: Any = inspect.Parameter.empty

    @property
    def required(self) -> bool:
        """Whether a request must give it: no default, and no demand to take the value from."""
        return self.default is inspect.Parameter.empty and self.link is None


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """One method exposed as a command.

    Marked by [command][flyball.foundation.device.commands.command].
    """

    name: str
    method: Callable[..., Any]
    params: dict[str, Param] = field(default_factory=dict)
    simulation: bool = False
    """Only meaningful on a simulated device -- a scripted fault, a disturbance. A UI keeps
    these on its simulation page, not beside the device's real commands."""
    commit: bool = False
    """The method only records; the rig commits the device afterwards. Most commands do their
    own I/O and need none."""
    mode: Any = None
    """What the device's `mode` output becomes when this runs, if it has one."""
    interrupts: bool = False
    """Puts a controller driving one of the device's demands into manual and runs (`stop`,
    a manual flow); without it, such a command is refused while the controller is active."""
    demand_of: str | None = None
    """For a synthesised `set_<name>`: the path of the demand it sets; the rig routes it through
    its demand path."""

    @property
    def doc(self) -> str | None:
        return self.method.__doc__


@overload
def command[F: Callable[..., Any]](fn: F, /) -> F: ...
@overload
def command[F: Callable[..., Any]](
    *,
    name: str | None = None,
    simulation: bool = False,
    commit: bool = False,
    mode: Any = None,
    interrupts: bool = False,
) -> Callable[[F], F]: ...
def command(
    fn: Any = None,
    /,
    *,
    name: str | None = None,
    simulation: bool = False,
    commit: bool = False,
    mode: Any = None,
    interrupts: bool = False,
) -> Any:
    """Mark a device method as a command, under the method's name or `name`.

    `@command` or `@command(name="stop")`. The method's signature is the
    command's; an argument annotated `Annotated[<type>, <descriptor>]` (or
    named like a descriptor) is a value for that demand -- legal in the
    class body, since the descriptor's name is already bound there. `mode`
    is what the device's `mode` output becomes when it runs. `commit=True`
    for a method that only records and needs the device committed after.
    `interrupts=True` puts a controller
    driving the device into manual and runs; without it the command is
    refused while one is active. `simulation=True` marks one that only
    makes sense on a simulated device (a scripted fault, a disturbance): it
    is served like any other, but the schema says so, so a UI can keep it
    off the device's page.
    """

    def mark(f: Any) -> Any:
        f.__command__ = name or f.__name__
        f.__command_options__ = {
            "simulation": simulation,
            "commit": commit,
            "mode": mode,
            "interrupts": interrupts,
        }
        return f

    return mark(fn) if fn is not None else mark
