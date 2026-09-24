"""Programs: checking a file, running it, and what the programmer is doing.

A file is a document in the server's [Dialect][flyball.interfaces.server.dialect.Dialect];
`check` normalises it to the internally tagged form without running anything,
which is what an editor wants back. `run` does the same and hands the result
to the programmer.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, TypeAdapter, ValidationError

from flyball.interfaces.server.commands import command_request, commands_schema
from flyball.interfaces.server.deps import DialectDep, ProgrammerDep, RigDep
from flyball.interfaces.server.dialect import (
    StepError,
    check_renamed,
    normalise_program,
    program_from_document,
    program_schema,
)
from flyball.sequencing import Program
from flyball.sequencing.programmer import ProgrammerState

router = APIRouter(prefix="/api/programs", tags=["programs"])


class ProgramCheck(BaseModel):
    """What ``check`` says: is the document accepted, and what does it name that the rig lacks."""

    ok: bool
    error: str | None = None
    normalised: Any = None
    warnings: dict[int, str] = {}
    """By step index: a controller, tuning or device the rig lacks right now. It may still run."""


@router.get("/schema")
async def read_program_schema(dialect: DialectDep) -> dict[str, Any]:
    """JSON schema for a program file in the server's dialect, for an editor."""
    return program_schema(dialect)


@router.get("/commands")
async def read_command_schema(dialect: DialectDep) -> dict[str, Any]:
    """The internally tagged request union as JSON schema, for building a form."""
    return commands_schema(dialect.steps)


@router.post("/check")
async def check_program(
    body: Annotated[Any, Body()], dialect: DialectDep, rig: RigDep
) -> ProgramCheck:
    """Normalise and validate a program document; nothing runs.

    Returns the internally tagged document, with a warning per step that
    names something the rig lacks. 422 names the step that failed.
    """
    try:
        normalised = normalise_program(body, dialect)
        program = program_from_document(body, dialect)
    except (StepError, ValidationError, TypeError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return ProgramCheck(ok=True, normalised=normalised, warnings=program.missing(rig))


@router.get("/running")
async def read_running(programmer: ProgrammerDep) -> ProgrammerState:
    return programmer.state


@router.post("/run")
def run_program(
    body: Annotated[Any, Body()],
    dialect: DialectDep,
    programmer: ProgrammerDep,
    cancel: bool = False,
) -> ProgrammerState:
    """Start a program document, in order.

    Not `async`: the first step is applied on this thread, so a step the
    rig cannot take is refused here. `cancel` cancels whatever is running
    instead of refusing with a conflict.
    """
    try:
        program = program_from_document(body, dialect)
    except (StepError, ValidationError, TypeError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    programmer.start(program, cancel=cancel)
    return programmer.state


@router.post("/command")
def run_command(
    body: dict[str, Any],
    dialect: DialectDep,
    programmer: ProgrammerDep,
    cancel: bool = False,
) -> ProgrammerState:
    """Apply one internally tagged command; a wait or ramp continues on the programmer's thread."""
    # Validated here rather than in the signature: the command union exists
    # only once commands have registered, which is after this module loads.
    try:
        rest = {key: value for key, value in body.items() if key != "type"}
        check_renamed(body.get("type"), rest, "type", dialect.steps)
    except StepError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    try:
        request = TypeAdapter(command_request(dialect.steps)).validate_python(body)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    programmer.start(request.parse(), cancel=cancel)
    return programmer.state


@router.post("/cancel")
def cancel(programmer: ProgrammerDep) -> ProgrammerState:
    """End the running program, as a person: it ends `cancelled`, and outputs are kept."""
    programmer.cancel()
    return programmer.state


_ = Program  # the type `program_from_document` returns; named for the docs
