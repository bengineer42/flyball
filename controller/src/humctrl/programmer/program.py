"""An ordered list of commands.

A program is data: what to do and in what order. It holds no cursor and no
running flag -- :class:`~humctrl.programmer.Programmer` owns those, so the same
program can be run twice, or twice at once on two rigs.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, Any, overload

if TYPE_CHECKING:
    from .command import Command


class Program(Sequence["Command[Any]"]):
    """The steps of a run, in order.

    Args:
        commands: The steps. Copied, so later edits to the caller's list cannot
            shift the sequence under a run in progress.
        name: What to call it in telemetry and logs.

    Raises:
        ValueError: ``commands`` is empty. A program that does nothing is a
            mistake at the point it was written, not something to discover
            halfway through a run.
    """

    __slots__ = ("_commands", "name")

    _commands: tuple[Command[Any], ...]
    name: str | None

    def __init__(self, commands: Sequence[Command[Any]], name: str | None = None) -> None:
        if not commands:
            raise ValueError("a program needs at least one command")
        self._commands = tuple(commands)
        self.name = name

    def get(self, index: int, default: None = None, /) -> Command[Any] | None:
        with suppress(IndexError):
            return self._commands[index]
        return default

    @overload
    def __getitem__(self, index: int) -> Command[Any]: ...
    @overload
    def __getitem__(self, index: slice) -> Program: ...
    def __getitem__(self, index: int | slice) -> Command[Any] | Program:
        if isinstance(index, slice):
            return Program(self._commands[index], self.name)
        return self._commands[index]

    def __len__(self) -> int:
        return len(self._commands)

    def __iter__(self) -> Iterator[Command[Any]]:
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


# A slice of an empty program is impossible: ``Program`` cannot be empty, and
# ``__getitem__`` would raise on ``program[5:]`` past the end. Slicing exists
# for resuming a part-finished run -- ``programmer.start(program[n:])`` -- so
# that case wants a guard at the call site rather than an empty program here.
