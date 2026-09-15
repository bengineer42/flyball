"""Readers: what each one is, how it is being run, and the commands its class marked."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body
from pydantic import TypeAdapter

from flyball.runtime.reader import ReaderRun
from flyball.server.deps import RigDep

from .devices import device_schema, run, source_schema

router = APIRouter(prefix="/api/readers", tags=["readers"])

RUN = TypeAdapter(ReaderRun)


def _view(rig: RigDep, name: str) -> dict[str, Any]:
    reader = rig.readers.get(name)
    return {
        "name": name,
        "view": reader.view,
        "run": RUN.dump_python(rig.readers.run(name), mode="json"),
        "sources": [str(source.name) for source in reader.sources],
    }


@router.get("")
def read_readers(rig: RigDep) -> dict[str, Any]:
    """Every attached reader: its view, how it is run, and what it reads."""
    return {name: _view(rig, name) for name in rig.readers.by_name}


@router.get("/{name}")
def read_reader(rig: RigDep, name: str) -> dict[str, Any]:
    return _view(rig, name)


@router.get("/{name}/schema")
def read_reader_schema(rig: RigDep, name: str) -> dict[str, Any]:
    reader = rig.readers.get(name)
    return device_schema(reader, sources=[source_schema(s) for s in reader.sources])


@router.post("/{name}/{command}")
def run_command(
    rig: RigDep, name: str, command: str, body: Annotated[dict[str, Any] | None, Body()] = None
) -> Any:
    """Call the marked method with the validated body; respond with whatever it returns."""
    return run(rig.readers.get(name), command, body)
