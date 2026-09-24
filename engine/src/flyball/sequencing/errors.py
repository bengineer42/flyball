from __future__ import annotations

from typing import TYPE_CHECKING

from flyball.foundation.errors import ConflictError

if TYPE_CHECKING:
    from .program import Program
    from .step import Activity, Step


class ProgramAlreadyRunningError(ConflictError):
    """A program is still running."""

    def __init__(self, current: Program | Step, new: Program | Step) -> None:
        self.current = current
        self.new = new
        super().__init__(
            "Attempted to start a new program while another is still running. "
            f"Current: {current}, New: {new}"
        )


class StepAlreadyRunningError(ConflictError):
    def __init__(self, current: Activity, new: Step) -> None:
        super().__init__(
            f"Step is already running ({current}). Cancel it before starting a new one ({new})."
        )


class StepRuntimeError(Exception):
    """A program failed while running."""

    def __init__(self, command: Step, step: int, error: Exception) -> None:
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
