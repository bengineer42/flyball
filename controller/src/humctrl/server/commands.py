"""Wire format for commands.

Each request model is derived from the command's own ``__init__``, so a command
is described once. :data:`WIRE_TYPES` covers the parameters whose domain types
cannot cross the wire as they stand: everything else is used verbatim, which
works for anything pydantic can describe.
"""

from __future__ import annotations

from inspect import signature
from typing import Any, Literal, get_type_hints

from pydantic import BaseModel, ConfigDict, TypeAdapter, create_model

from humctrl.core.clock import Duration, Rate, Time
from humctrl.control import ControlLaw, ControlLawConfig
from humctrl.control.types import ControlLawView, Tuning
from humctrl.programmer.commands import Command
from humctrl.pumps.types import BlendFlow
from humctrl.server.schemas import (
    BlendFlowRequest,
    DurationRequest,
    LawConfig,
    RateRequest,
    generate_command_schema,
)

#: Domain annotation -> how it crosses the wire. A parameter whose annotation is
#: not a key here keeps its own type. Keys are matched whole, so a union must be
#: written exactly as the command declares it.
WIRE_TYPES: dict[Any, Any] = {
    BlendFlow: BlendFlowRequest,
    BlendFlow | None: BlendFlowRequest | None,
    Duration | float: DurationRequest | float,
    Rate | Time | Duration: RateRequest | DurationRequest,
    # A running law cannot cross the wire, so the tuning unions narrow to a
    # config or the name of a stored one.
    Tuning | ControlLaw | ControlLawConfig | ControlLawView | str | None: LawConfig | str | None,
    Tuning | ControlLaw | ControlLawConfig | str | None: LawConfig | str | None,
}


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
    hints = get_type_hints(command.__init__)
    fields: dict[str, Any] = {"command": (Literal[command.tag], command.tag)}
    for name, parameter in signature(command).parameters.items():
        annotation = hints.get(name, Any)
        fields[name] = (
            WIRE_TYPES.get(annotation, annotation),
            ... if parameter.default is parameter.empty else parameter.default,
        )
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
CommandRequest = generate_command_schema(Commands, "command", request_for)

#: The same union as a JSON schema, for a client building a command form.
CommandsSchema = TypeAdapter(CommandRequest).json_schema()
