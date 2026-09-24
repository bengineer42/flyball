"""An ordered list of commands.

A program is data with no cursor or running flag;
[Programmer][flyball.sequencing.Programmer] owns those, so one program can
run twice, or on two rigs at once.

The library's own steps, each a `Step` subclass whose wire form (and the
program file's JSON schema) is derived from its constructor by
[flyball.interfaces.server.dialect][]: `regulate`/`ramp`/`wait`/`settle`/`manual` name a
controller by its output address, or a list, or none for the rig's default
(`sequencing/loops.py`); `set` puts values on one device's writable signals
as a demand, and `command` calls one of a device's own commands
(`sequencing/devices.py`); `prompt` pauses for an operator or an external
trigger (`sequencing/activities.py`).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    from flyball.rig import Rig

    from .step import Step


class Program(Sequence["Step"]):
    """The steps of a run, in order. `commands` is copied.

    Raises:
        ValueError: `commands` is empty.
    """

    __slots__ = ("_commands", "description", "name")

    _commands: tuple[Step, ...]
    name: str | None
    description: str | None
    """What the program is for, for a listing; optional."""

    def __init__(
        self, commands: Sequence[Step], name: str | None = None, description: str | None = None
    ) -> None:
        if not commands:
            raise ValueError("a program needs at least one command")
        self._commands = tuple(commands)
        self.name = name
        self.description = description

    def missing(self, rig: Rig) -> dict[int, str]:
        """What each step names that `rig` lacks right now, by step index; see `Step.missing`."""
        out: dict[int, str] = {}
        for index, command in enumerate(self._commands):
            if gaps := command.missing(rig):
                out[index] = "; ".join(gaps)
        return out

    def get(self, index: int, default: None = None, /) -> Step | None:
        with suppress(IndexError):
            return self._commands[index]
        return default

    @overload
    def __getitem__(self, index: int) -> Step: ...
    @overload
    def __getitem__(self, index: slice) -> Program: ...
    def __getitem__(self, index: int | slice) -> Step | Program:
        if isinstance(index, slice):
            return Program(self._commands[index], self.name)
        return self._commands[index]

    def __len__(self) -> int:
        return len(self._commands)

    def __iter__(self) -> Iterator[Step]:
        return iter(self._commands)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Program):
            return NotImplemented
        return self._commands == other._commands

    def __hash__(self) -> int:
        return hash(self._commands)

    @property
    def types(self) -> tuple[str, ...]:
        """The steps by type, for display."""
        return tuple(command.type for command in self._commands)

    def __repr__(self) -> str:
        name = f"{self.name!r}, " if self.name is not None else ""
        return f"Program({name}{len(self)} steps: {' -> '.join(self.types)})"


# A slice of an empty program is impossible: `Program` cannot be empty, and
# `__getitem__` would raise on `program[5:]` past the end. Slicing exists
# for resuming a part-finished run -- `programmer.start(program[n:])` -- so
# that case wants a guard at the call site rather than an empty program here.
