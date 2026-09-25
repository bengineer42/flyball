"""Wire format for commands.

Each request model is derived from the command's `__init__`, so a command is
described once. [WIRE_TYPES][flyball.interfaces.server.wire.WIRE_TYPES] substitutes the
domain types that cannot cross the wire; everything else is used verbatim.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter, create_model

from flyball.foundation.schema import Titled
from flyball.interfaces.server.schemas import discriminated_union
from flyball.interfaces.server.wire import WIRE_TYPES, wire_fields
from flyball.sequencing.step import Step

__all__ = ["WIRE_TYPES", "CommandBase", "command_request", "commands_schema", "request_for"]


class CommandBase(BaseModel):
    """Shared by every generated request. `type` is narrowed per command."""

    model_config = ConfigDict(extra="forbid")

    type: str
    _command_cls: ClassVar[type[Step]]
    """The command this request describes -- set once, on the subclass `request_model` builds."""

    def parse(self) -> Step:
        """The domain command this request describes, nested requests parsed."""
        fields = {
            name: value.parse() if hasattr(value, "parse") else value
            for name, value in self
            if name != "type"
        }
        return self._command_cls(**fields)


def request_model(command: type[Step]) -> type[CommandBase]:
    """The pydantic request for `command`: one field per constructor parameter, plus its type."""
    fields: dict[str, Any] = {"type": (Literal[command.type], command.type)}
    fields.update(wire_fields(command))
    model = create_model(f"{command.__name__}Request", __base__=CommandBase, **fields)
    model._command_cls = command
    return model


_REQUESTS: dict[type[Step], type[CommandBase]] = {}


def request_for(command: type[Step]) -> type[CommandBase]:
    """The pydantic request for `command`, built once and cached.

    Deferred because `__init_subclass__` runs before `@dataclass` generates
    the constructor.
    """
    if command not in _REQUESTS:
        _REQUESTS[command] = request_model(command)
    return _REQUESTS[command]


def command_request(commands: Mapping[str, type[Step]]) -> Any:
    """`commands` (a dialect's, or a catalog's) as one request type, discriminated by type."""
    if not commands:
        raise LookupError("no commands are registered")
    return discriminated_union(commands, "type", request_for)


def commands_schema(commands: Mapping[str, type[Step]]) -> dict[str, Any]:
    """The request union as JSON schema, for a client building a command form."""
    return TypeAdapter(command_request(commands)).json_schema(schema_generator=Titled)
