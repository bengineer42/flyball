"""An ordered list of commands.

A program is data with no cursor or running flag;
[Programmer][flyball.programmer.Programmer] owns those, so one program can
run twice, or on two rigs at once.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    from flyball.runtime.rig import Rig

    from .command import Command


class Program(Sequence["Command"]):
    """The steps of a run, in order. `commands` is copied.

    Raises:
        ValueError: `commands` is empty.
    """

    __slots__ = ("_commands", "description", "name")

    _commands: tuple[Command, ...]
    name: str | None
    description: str | None
    """What the program is for, for a listing; optional."""

    def __init__(
        self, commands: Sequence[Command], name: str | None = None, description: str | None = None
    ) -> None:
        if not commands:
            raise ValueError("a program needs at least one command")
        self._commands = tuple(commands)
        self.name = name
        self.description = description

    def missing(self, rig: Rig) -> dict[int, str]:
        """What each step names that `rig` lacks right now, by step index; see `Command.missing`."""
        out = {}
        for index, command in enumerate(self._commands):
            if gaps := command.missing(rig):
                out[index] = "; ".join(gaps)
        return out

    def get(self, index: int, default: None = None, /) -> Command | None:
        with suppress(IndexError):
            return self._commands[index]
        return default

    @overload
    def __getitem__(self, index: int) -> Command: ...
    @overload
    def __getitem__(self, index: slice) -> Program: ...
    def __getitem__(self, index: int | slice) -> Command | Program:
        if isinstance(index, slice):
            return Program(self._commands[index], self.name)
        return self._commands[index]

    def __len__(self) -> int:
        return len(self._commands)

    def __iter__(self) -> Iterator[Command]:
        return iter(self._commands)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Program):
            return NotImplemented
        return self._commands == other._commands

    def __hash__(self) -> int:
        return hash(self._commands)

    @property
    def tags(self) -> tuple[str, ...]:
        """The steps by tag, for display."""
        return tuple(command.tag for command in self._commands)

    def __repr__(self) -> str:
        name = f"{self.name!r}, " if self.name is not None else ""
        return f"Program({name}{len(self)} steps: {' -> '.join(self.tags)})"


# A slice of an empty program is impossible: `Program` cannot be empty, and
# `__getitem__` would raise on `program[5:]` past the end. Slicing exists
# for resuming a part-finished run -- `programmer.start(program[n:])` -- so
# that case wants a guard at the call site rather than an empty program here.
