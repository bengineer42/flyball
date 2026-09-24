"""The program library: documents kept as written, run by name.

A program is stored verbatim in the format it arrived in -- YAML, TOML or
JSON -- and every save under a name adds a version. Reading converts on
demand: ``?format=`` on a download re-emits the document tree in another
format (comments do not survive that; the stored text keeps them).
"""

from __future__ import annotations

import hashlib
from functools import partial
from pathlib import Path
from typing import Annotated, Any

from anyio import to_thread
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, TypeAdapter, ValidationError

from flyball.foundation.files import SUFFIXES, loads
from flyball.foundation.keys import is_key
from flyball.interfaces.server.deps import (
    DialectDep,
    ProgrammerDep,
    RigDep,
    StoreDep,
    current_programs_dir,
)
from flyball.interfaces.server.dialect import StepError, normalise_program, program_from_document
from flyball.interfaces.server.formats import MEDIA_TYPES, FormatError, detect, dump, parse
from flyball.interfaces.server.routes.program import ProgramCheck
from flyball.library.tunings import Tuning
from flyball.record import ProgramFormat, ProgramRow
from flyball.record.errors import ProgramNotFoundError
from flyball.record.store import Store
from flyball.rig import Rig
from flyball.runtime.config import LawConfig
from flyball.sequencing.programmer import ProgrammerState

router = APIRouter(prefix="/api/programs/library", tags=["programs"])


class SaveProgram(BaseModel):
    """JSON form of a save: the document text and what it is written in."""

    format: ProgramFormat
    body: str
    notes: Any = None
    """What the author says about this version: free text, or any JSON."""


def _check(row: ProgramRow, dialect: Any, rig: Rig) -> ProgramCheck:
    try:
        document = parse(row.body, row.format)
        normalised = normalise_program(document, dialect)
        program = program_from_document(document, dialect)
    except (FormatError, StepError, ValidationError, TypeError, ValueError) as e:
        return ProgramCheck(ok=False, error=str(e))
    return ProgramCheck(ok=True, normalised=normalised, warnings=program.missing(rig))


def load_tunings(rig: Rig, directory: Path) -> list[str]:
    """Store every control-law config file in `directory` on `rig.tunings`, under its stem.

    Each `*.toml`/`*.yaml`/`*.json` file holds one law config, validated
    against the same discriminated union (`LawConfig`) a controller's own `tuning`
    field parses. A missing directory is fine: no tunings.
    """
    if not directory.is_dir():
        return []
    adapter = TypeAdapter(LawConfig)
    loaded: list[str] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in SUFFIXES:
            continue
        config = adapter.validate_python(loads(path.read_text(encoding="utf-8"), path.suffix))
        rig.tunings.add(Tuning(name=path.stem, config=config))
        loaded.append(path.stem)
    return loaded


def import_directory(store: Store, directory: Path, now_ns: int) -> list[ProgramRow]:
    """Bring every program file in `directory` into the library.

    The file's stem is the name (`dry-then-hold.yaml` is `dry_then_hold`, D-079). A
    file whose text matches the newest stored version is left alone; a changed file
    becomes a new version, so editing on disk and in the UI share one history. Files
    that are not YAML, TOML or JSON are ignored; one that does not parse, or whose
    stem is not a key, is skipped, not fatal.
    """
    imported: list[ProgramRow] = []
    for path in sorted(directory.iterdir()):
        fmt = detect(None, path.name)
        if fmt is None or not path.is_file() or not is_key(path.stem):
            continue
        text = path.read_text(encoding="utf-8")
        try:
            parse(text, fmt)
        except FormatError:
            continue
        try:
            newest = store.program(path.stem)
            if newest.sha256 == hashlib.sha256(text.encode()).hexdigest():
                continue
        except ProgramNotFoundError:
            pass
        imported.append(
            store.save_program(path.stem, fmt, text, now_ns, notes={"source": str(path)})
        )
    return imported


@router.get("")
def read_programs(store: StoreDep) -> list[ProgramRow]:
    """Newest version of every name."""
    return store.programs()


@router.post("/import")
def import_programs(store: StoreDep, rig: RigDep) -> list[ProgramRow]:
    """Rescan the programs directory; returns what was newly imported. Empty when none is set."""
    directory = current_programs_dir()
    if directory is None or not directory.is_dir():
        return []
    return import_directory(store, directory, rig.clock.now_ns())


@router.get("/formats")
async def read_formats() -> dict[str, str]:
    """Format name -> media type, for an upload dialog."""
    return {str(name): media for name, media in MEDIA_TYPES.items()}


@router.get("/{name}")
def read_program(store: StoreDep, name: str) -> ProgramRow:
    return store.program(name)


@router.get("/{name}/check")
def check_stored(store: StoreDep, dialect: DialectDep, rig: RigDep, name: str) -> ProgramCheck:
    """Whether the newest version still parses for this rig, and what it names that it lacks."""
    return _check(store.program(name), dialect, rig)


@router.get("/{name}/history")
def read_program_history(store: StoreDep, name: str) -> list[ProgramRow]:
    return store.program_history(name)


@router.get("/{name}/download")
def download_program(
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
    notes: Annotated[
        str | None, Query(description="what the author says about this version")
    ] = None,
) -> ProgramRow:
    """Add a version: the document itself (YAML/TOML/JSON media type) or a JSON ``SaveProgram``.

    The text is stored as sent; it is parsed once to make sure it is at least
    a document in the format claimed, but not validated against the dialect --
    ``check`` does that, and a program that no longer validates is still
    worth keeping.
    """
    raw = (await request.body()).decode()
    fmt = detect(content_type, name)
    if detect(None, name) is not None:  # `x.toml` is the program `x`, written in TOML
        name = name.rsplit(".", 1)[0]
    said: Any = notes
    if content_type and content_type.split(";")[0].strip() == "application/json":
        # Either a SaveProgram envelope or a bare JSON program: the envelope has `body`.
        try:
            envelope = SaveProgram.model_validate_json(raw)
        except ValidationError:
            envelope = None
        if envelope is not None:
            fmt, raw = envelope.format, envelope.body
            said = envelope.notes if envelope.notes is not None else notes
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
    # Async to read the body; the store call goes to a thread, never on the loop.
    save = partial(store.save_program, name, fmt, raw, rig.clock.now_ns(), notes=said)
    return await to_thread.run_sync(save)


@router.delete("/{name}", status_code=204)
def delete_program(store: StoreDep, name: str) -> None:
    """Every version."""
    store.delete_program(name)


class Rename(BaseModel):
    name: str


@router.post("/{name}/rename")
def rename_program(store: StoreDep, name: str, body: Rename) -> list[ProgramRow]:
    """Move the program -- every version, its whole history -- under a new name. 409 if taken."""
    return store.rename_program(name, body.name)


@router.post("/{name}/run")
def run_stored(
    store: StoreDep,
    dialect: DialectDep,
    programmer: ProgrammerDep,
    rig: RigDep,
    name: str,
    cancel: bool = False,
    version: Annotated[int | None, Query()] = None,
) -> ProgrammerState:
    """Run the newest version (or ``version``); the run is noted as an event naming the version."""
    row = store.program_version(version) if version is not None else store.program(name)
    try:
        program = program_from_document(parse(row.body, row.format), dialect)
    except (FormatError, StepError, ValidationError, TypeError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    from flyball.foundation.device import Code, Scope, Severity

    rig.event(
        Severity.INFO,
        Scope.PROGRAM,
        name,
        Code.RUN_FROM_LIBRARY,
        f"running {name} (version {row.id})",
        {"program_id": row.id, "sha256": row.sha256, "format": row.format},
    )
    programmer.start(program, cancel=cancel)
    return programmer.state
