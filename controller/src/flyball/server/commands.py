"""Wire format for commands.

Each request model is derived from the command's own ``__init__``, so a command
is described once. :data:`WIRE_TYPES` covers the parameters whose domain types
cannot cross the wire as they stand: everything else is used verbatim, which
works for anything pydantic can describe.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter, create_model

from flyball.programmer.command import Command, Commands
from flyball.server.schemas import discriminated_union
from flyball.server.wire import WIRE_TYPES, wire_fields

__all__ = ["WIRE_TYPES", "CommandBase", "CommandRequest", "CommandsSchema", "request_for"]


class CommandBase(BaseModel):
    """Shared by every generated request. ``command`` is narrowed per command."""

    model_config = ConfigDict(extra="forbid")

    command: str

    def parse(self) -> Command:
        """The command this request describes, with nested requests parsed.

        Returns:
            The domain command, built by keyword so field order cannot slip.
        """
        fields = {
            name: value.parse() if hasattr(value, "parse") else value
            for name, value in self
            if name != "command"
        }
        return Commands[self.command](**fields)


def request_model(command: type[Command]) -> type[CommandBase]:
    """The pydantic request for ``command``, derived from its ``__init__``.

    Args:
        command: The command class to describe.

    Returns:
        A model with one field per constructor parameter, plus the ``command``
        tag as a ``Literal`` so a union can discriminate on it.
    """
    fields: dict[str, Any] = {"command": (Literal[command.tag], command.tag)}
    fields.update(wire_fields(command))
    return create_model(f"{command.__name__}Request", __base__=CommandBase, **fields)


_REQUESTS: dict[type[Command], type[CommandBase]] = {}


def request_for(command: type[Command]) -> type[CommandBase]:
    """The pydantic request for ``command``, built once and cached.

    Deferred rather than built when the command is defined: ``__init_subclass__``
    runs before ``@dataclass`` generates the constructor this derives from.
    """
    if command not in _REQUESTS:
        _REQUESTS[command] = request_model(command)
    return _REQUESTS[command]


#: Every registered command, discriminated by its tag.
CommandRequest = discriminated_union(Commands, "command", request_for)

#: The same union as a JSON schema, for a client building a command form.
CommandsSchema = TypeAdapter(CommandRequest).json_schema()
