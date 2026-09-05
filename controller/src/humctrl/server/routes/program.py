"""Commands and programs: what a rig can be told to do, and running a list of it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from humctrl.server.commands import CommandRequest, CommandsSchema
from humctrl.server.deps import ManagerDep
from humctrl.state import State

command_router = APIRouter(prefix="/api/command", tags=["controller"])


@command_router.get("/schema")
async def read_command_schema() -> dict[str, Any]:
    """The JSON schema for every registered command, for building a form.

    ``discriminator.mapping`` lists the commands and points at the fields of
    each, so a client needs no second copy of what a command looks like.
    """
    return CommandsSchema


@command_router.post("/")
def run_command(body: CommandRequest, manager: ManagerDep, interrupt: bool = False) -> State:
    """Run one command, returning the rig state it produced.

    Returns as soon as the command has been applied: a hold or ramp continues
    on its own thread, and the client watches the rest over ``/ws/telemetry``.

    Not ``async``: applying a command writes to the pumps, and that blocking
    I/O on the event loop would stall the telemetry sockets. A plain ``def``
    route runs in a threadpool instead.

    Args:
        body: The command to run.
        manager: The attached rig.
        interrupt: ``?interrupt=true`` stops whatever is running first, rather
            than refusing with a conflict. A query parameter rather than a field
            on the command: it says how to apply this request, and would be
            meaningless replayed as a step inside a stored program.
    """
    return manager.start_command(body.parse(), interrupt=interrupt)
