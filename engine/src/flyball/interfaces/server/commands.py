"""Wire format for commands.

Each request model is derived from the command's `__init__`, so a command is
described once. [WIRE_TYPES][flyball.server.wire.WIRE_TYPES] substitutes the
domain types that cannot cross the wire; everything else is used verbatim.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter, create_model

from flyball.interfaces.server.schemas import discriminated_union
from flyball.interfaces.server.wire import WIRE_TYPES, wire_fields
from flyball.programmer.command import Command, Commands

__all__ = ["WIRE_TYPES", "CommandBase", "command_request", "commands_schema", "request_for"]


class CommandBase(BaseModel):
    """Shared by every generated request. `command` is narrowed per command."""

    model_config = ConfigDict(extra="forbid")

    command: str

    def parse(self) -> Command:
        """The domain command this request describes, nested requests parsed."""
        fields = {
            name: value.parse() if hasattr(value, "parse") else value
            for name, value in self
            if name != "command"
        }
        return Commands[self.command](**fields)


def request_model(command: type[Command]) -> type[CommandBase]:
    """The pydantic request for `command`: one field per constructor parameter, plus its tag."""
    fields: dict[str, Any] = {"command": (Literal[command.tag], command.tag)}
    fields.update(wire_fields(command))
    return create_model(f"{command.__name__}Request", __base__=CommandBase, **fields)


_REQUESTS: dict[type[Command], type[CommandBase]] = {}


def request_for(command: type[Command]) -> type[CommandBase]:
    """The pydantic request for `command`, built once and cached.

    Deferred because `__init_subclass__` runs before `@dataclass` generates
    the constructor.
    """
    if command not in _REQUESTS:
        _REQUESTS[command] = request_model(command)
    return _REQUESTS[command]


def command_request() -> Any:
    """Every registered command as one request type, discriminated by tag. Built on demand."""
    if not Commands:
        raise LookupError("no commands are registered")
    return discriminated_union(Commands, "command", request_for)


def commands_schema() -> dict[str, Any]:
    """The request union as JSON schema, for a client building a command form."""
    return TypeAdapter(command_request()).json_schema()
