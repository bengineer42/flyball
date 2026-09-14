"""Commands and programs: what a rig can be told to do, and running a list of it.

Not mounted yet: ``humctrl.programmer`` does not import (a dangling ``from`` in
its ``__init__`` and a stale ``humctrl.resource`` import). Once it does, add
``program_router`` to ``routes/__init__.py`` and ``app.py``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from humctrl.programmer import Program
from humctrl.programmer.programmer import ProgrammerState
from humctrl.server.commands import CommandRequest, CommandsSchema
from humctrl.server.deps import ProgrammerDep

program_router = APIRouter(prefix="/api/program", tags=["program"])


@program_router.get("/commands/schema")
async def read_command_schema() -> dict[str, Any]:
    """The JSON schema for every registered command, for building a form."""
    return CommandsSchema


@program_router.get("")
async def read_program(programmer: ProgrammerDep) -> ProgrammerState:
    return programmer.state


@program_router.post("/command")
def run_command(
    body: CommandRequest,  # type: ignore[valid-type]
    programmer: ProgrammerDep,
    interrupt: bool = False,
) -> ProgrammerState:
    """Apply one command and return as soon as it has been applied.

    A hold or ramp carries on on the programmer's thread; watch it over the
    websockets. Not ``async``: applying a command may write to hardware.

    ``interrupt`` stops whatever is running first rather than refusing with a
    conflict. A query parameter rather than a field: it says how to apply
    this request, and means nothing replayed as a step in a stored program.
    """
    programmer.start(body.parse(), interrupt=interrupt)
    return programmer.state


@program_router.post("")
def run_program(
    body: list[CommandRequest],  # type: ignore[valid-type]
    programmer: ProgrammerDep,
    interrupt: bool = False,
) -> ProgrammerState:
    """Start a list of commands, in order."""
    programmer.start(Program([step.parse() for step in body]), interrupt=interrupt)
    return programmer.state


@program_router.post("/interrupt")
def interrupt(programmer: ProgrammerDep) -> ProgrammerState:
    programmer.interrupt()
    return programmer.state
