from __future__ import annotations

from typing import TYPE_CHECKING

from flyball.foundation.errors import ConflictError

if TYPE_CHECKING:
    from .command import Activity, Command
    from .program import Program


class ProgramAlreadyRunningError(ConflictError):
    """A program is still running."""

    def __init__(self, current: Program | Command, new: Program | Command) -> None:
        self.current = current
        self.new = new
        super().__init__(
            "Attempted to start a new program while another is still running. "
            f"Current: {current}, New: {new}"
        )


class CommandAlreadyRunningError(ConflictError):
    def __init__(self, current: Activity, new: Command) -> None:
        super().__init__(
            f"Command is already running ({current}). Interrupt it before starting a new one "
            f"({new})."
        )


class CommandRuntimeError(Exception):
    """A program failed while running."""

    def __init__(self, command: Command, step: int, error: Exception) -> None:
        self.command = command
        self.step = step
        self.error = error
        super().__init__(f"Error in command {command} at step {step}: {error}")


class ProgramFinishedError(Exception):
    """The program has already finished."""

    def __init__(self, program: Program, step: int) -> None:
        self.program = program
        message = (
            f"Step {step} on a program that has already finished (total steps: {len(program)})."
        )
        super().__init__(message)
