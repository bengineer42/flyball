"""Commands and programs: what a rig can be told to do, and running a list of it.

Not mounted yet: :class:`~flyball.programmer.Programmer` still calls
``rig.publish_warning``, which does not exist until the events stream lands.
Once it runs, add ``program_router`` to ``routes/__init__.py`` and ``app.py``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import TypeAdapter

from flyball.programmer import Program
from flyball.programmer.programmer import ProgrammerState
from flyball.server.commands import command_request, commands_schema
from flyball.server.deps import ProgrammerDep

program_router = APIRouter(prefix="/api/program", tags=["program"])


@program_router.get("/commands/schema")
async def read_command_schema() -> dict[str, Any]:
    """The JSON schema for every registered command, for building a form."""
    return commands_schema()


@program_router.get("")
async def read_program(programmer: ProgrammerDep) -> ProgrammerState:
    return programmer.state


@program_router.post("/command")
def run_command(
    body: dict[str, Any],
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
    # Validated here rather than in the signature: the command union exists
    # only once commands have registered, which is after this module loads.
    request = TypeAdapter(command_request()).validate_python(body)
    programmer.start(request.parse(), interrupt=interrupt)
    return programmer.state


@program_router.post("")
def run_program(
    body: list[dict[str, Any]],
    programmer: ProgrammerDep,
    interrupt: bool = False,
) -> ProgrammerState:
    """Start a list of commands, in order."""
    requests = TypeAdapter(list[command_request()]).validate_python(body)  # type: ignore[misc]
    programmer.start(Program([step.parse() for step in requests]), interrupt=interrupt)
    return programmer.state


@program_router.post("/interrupt")
def interrupt(programmer: ProgrammerDep) -> ProgrammerState:
    programmer.interrupt()
    return programmer.state
