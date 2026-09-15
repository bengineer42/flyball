"""The program library: documents kept as written, run by name.

A program is stored verbatim in the format it arrived in -- YAML, TOML or
JSON -- and every save under a name adds a version. Reading converts on
demand: ``?format=`` on a download re-emits the document tree in another
format (comments do not survive that; the stored text keeps them).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError

from flyball.db import ProgramFormat, ProgramRow
from flyball.programmer.programmer import ProgrammerState
from flyball.server.deps import DialectDep, ProgrammerDep, RigDep, StoreDep
from flyball.server.dialect import StepError, normalise_program, program_from_document
from flyball.server.formats import MEDIA_TYPES, FormatError, detect, dump, parse

router = APIRouter(prefix="/api/programs/library", tags=["programs"])


class SaveProgram(BaseModel):
    """JSON form of a save: the document text and what it is written in."""

    format: ProgramFormat
    body: str
    label: str | None = None
    notes: Any = None


class ProgramCheck(BaseModel):
    """What ``check`` says about a stored version."""

    ok: bool
    error: str | None = None
    normalised: Any = None


def _check(row: ProgramRow, dialect: Any) -> ProgramCheck:
    try:
        document = parse(row.body, row.format)
        normalised = normalise_program(document, dialect)
        program_from_document(document, dialect)
    except (FormatError, StepError, ValidationError, TypeError, ValueError) as e:
        return ProgramCheck(ok=False, error=str(e))
    return ProgramCheck(ok=True, normalised=normalised)


@router.get("")
async def read_programs(store: StoreDep) -> list[ProgramRow]:
    """Newest version of every name."""
    return store.programs()


@router.get("/formats")
async def read_formats() -> dict[str, str]:
    """Format name -> media type, for an upload dialog."""
    return dict(MEDIA_TYPES)


@router.get("/{name}")
async def read_program(store: StoreDep, name: str) -> ProgramRow:
    return store.program(name)


@router.get("/{name}/check")
async def check_stored(store: StoreDep, dialect: DialectDep, name: str) -> ProgramCheck:
    """Whether the newest version still parses and validates against this rig's dialect."""
    return _check(store.program(name), dialect)


@router.get("/{name}/history")
async def read_program_history(store: StoreDep, name: str) -> list[ProgramRow]:
    return store.program_history(name)


@router.get("/{name}/download")
async def download_program(
    store: StoreDep,
    name: str,
    format: Annotated[ProgramFormat | None, Query()] = None,
    version: Annotated[int | None, Query(description="a version id from the history")] = None,
) -> Response:
    """The document as a file: verbatim in its own format, or converted to ``format``."""
    row = store.program_version(version) if version is not None else store.program(name)
    if row.name != name:
        raise HTTPException(status_code=404, detail=f"version {version} is not of {name!r}")
    wanted: ProgramFormat = format or row.format
    try:
        text = row.body if wanted == row.format else dump(parse(row.body, row.format), wanted)
    except FormatError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    filename = f"{name}.{'yml' if wanted == 'yaml' else wanted}"
    return Response(
        content=text,
        media_type=MEDIA_TYPES[wanted],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.put("/{name}", status_code=201)
async def save_program(
    request: Request,
    store: StoreDep,
    rig: RigDep,
    name: str,
    content_type: Annotated[str | None, Header()] = None,
    label: Annotated[str | None, Query()] = None,
) -> ProgramRow:
    """Add a version: the document itself (YAML/TOML/JSON media type) or a JSON ``SaveProgram``.

    The text is stored as sent; it is parsed once to make sure it is at least
    a document in the format claimed, but not validated against the dialect --
    ``check`` does that, and a program that no longer validates is still
    worth keeping.
    """
    raw = (await request.body()).decode()
    fmt = detect(content_type, name)
    notes: Any = None
    if content_type and content_type.split(";")[0].strip() == "application/json":
        # Either a SaveProgram envelope or a bare JSON program: the envelope has `body`.
        try:
            envelope = SaveProgram.model_validate_json(raw)
        except ValidationError:
            envelope = None
        if envelope is not None:
            fmt, raw, notes = envelope.format, envelope.body, envelope.notes
            label = envelope.label or label
    if fmt is None:
        raise HTTPException(
            status_code=415,
            detail="say what the document is: Content-Type one of "
            + ", ".join(MEDIA_TYPES.values()),
        )
    try:
        parse(raw, fmt)
    except FormatError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return store.save_program(name, fmt, raw, rig.clock.now_ns(), label=label, notes=notes)


@router.delete("/{name}", status_code=204)
async def delete_program(store: StoreDep, name: str) -> None:
    """Every version."""
    store.delete_program(name)


@router.post("/{name}/run")
def run_stored(
    store: StoreDep,
    dialect: DialectDep,
    programmer: ProgrammerDep,
    rig: RigDep,
    name: str,
    interrupt: bool = False,
    version: Annotated[int | None, Query()] = None,
) -> ProgrammerState:
    """Run the newest version (or ``version``); the run is noted as an event naming the version."""
    row = store.program_version(version) if version is not None else store.program(name)
    try:
        program = program_from_document(parse(row.body, row.format), dialect)
    except (FormatError, StepError, ValidationError, TypeError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    from flyball.core.device import Level

    rig.event(
        Level.INFO,
        "program",
        name,
        "run_from_library",
        f"running {name} (version {row.id})",
        {"program_id": row.id, "sha256": row.sha256, "format": row.format},
    )
    programmer.start(program, interrupt=interrupt)
    return programmer.state
