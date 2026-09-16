"""Dashboards: what the UI shows and how, saved per rig.

A dashboard is a document the UI owns -- a grid of widgets, each bound to a
channel, loop, actuator or nothing -- validated here only in outline, so the
widget catalogue can grow without a server release. The server keeps every
version under a name, like programs; the newest is what `GET` returns. A rig
can ship dashboards as ``dashboards/*.json`` beside its file: they are
imported on start, and an edited file becomes a new version.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from flyball.db import DashboardRow
from flyball.db.errors import DashboardNotFoundError
from flyball.db.store import Store
from flyball.server.deps import RigDep, StoreDep

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboards", tags=["dashboards"])


class Widget(BaseModel):
    """One tile on the grid. `config` is the widget kind's own business."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str = Field(description="Which widget: `readout`, `chart`, `loop`, ...")
    title: str | None = None
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=1)
    h: int = Field(ge=1)
    config: dict[str, Any] = Field(default_factory=dict)


class Grid(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cols: int = Field(default=12, ge=1, le=48)
    row_height: int = Field(default=40, ge=8, description="Pixels per grid row.")


class Dashboard(BaseModel):
    """The document. `name` is the key it is saved under; `rig` which rig it was made for."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    name: str = Field(min_length=1)
    rig: str
    description: str | None = None
    grid: Grid = Field(default_factory=Grid)
    widgets: list[Widget] = Field(default_factory=list)


class Rename(BaseModel):
    name: str = Field(min_length=1)


def import_directory(store: Store, directory: Path, rig: str, now_ns: int) -> list[DashboardRow]:
    """Bring every ``*.json`` dashboard in `directory` into the store, for `rig`.

    The file's stem is the name; the document's own `name`/`rig` are set from
    it. An unchanged file is left alone; an invalid one is skipped, not fatal.
    """
    imported: list[DashboardRow] = []
    for path in sorted(directory.glob("*.json")):
        if not path.is_file():
            continue
        try:
            document = Dashboard.model_validate({
                **json.loads(path.read_text()),
                "name": path.stem,
                "rig": rig,
            })
        except (ValueError, TypeError) as e:
            log.warning("dashboard %s skipped: %s", path, e)
            continue
        body = document.model_dump(mode="json")
        digest = hashlib.sha256(json.dumps(body, separators=(",", ":")).encode()).hexdigest()
        try:
            if store.dashboard(path.stem).sha256 == digest:
                continue
        except DashboardNotFoundError:
            pass
        imported.append(store.save_dashboard(path.stem, rig, body, now_ns))
    return imported


@router.get("/schema")
async def read_dashboard_schema() -> dict[str, Any]:
    """JSON Schema of the document, for an editor or an import check."""
    return Dashboard.model_json_schema()


@router.get("")
async def list_dashboards(
    store: StoreDep,
    rig: RigDep,
    every: bool = Query(False, description="Every rig's, not only this one's."),
) -> list[DashboardRow]:
    """The newest version of each dashboard, by name; this rig's unless ``every``."""
    return store.dashboards(None if every else rig.name)


@router.get("/{name}")
async def read_dashboard(store: StoreDep, name: str) -> DashboardRow:
    return store.dashboard(name)


@router.get("/{name}/history")
async def read_dashboard_history(store: StoreDep, name: str) -> list[DashboardRow]:
    """Every version, newest first."""
    return store.dashboard_history(name)


@router.put("/{name}", status_code=201)
async def save_dashboard(store: StoreDep, rig: RigDep, name: str, body: Dashboard) -> DashboardRow:
    """Save a version under `name`; the document's `name` and `rig` are overwritten to match."""
    document = body.model_copy(update={"name": name, "rig": body.rig or rig.name})
    return store.save_dashboard(
        name, document.rig, document.model_dump(mode="json"), rig.clock.now_ns()
    )


@router.post("/{name}/rename")
async def rename_dashboard(store: StoreDep, name: str, body: Rename) -> list[DashboardRow]:
    """Move every version under a new name. 409 if taken."""
    rows = store.rename_dashboard(name, body.name)
    # The document names itself too: keep the newest in step with its key.
    newest = rows[0]
    if isinstance(newest.body, dict) and newest.body.get("name") != body.name:
        store.save_dashboard(
            body.name, newest.rig, {**newest.body, "name": body.name}, newest.created_ns + 1
        )
        rows = store.dashboard_history(body.name)
    return rows


@router.delete("/{name}", status_code=204)
async def delete_dashboard(store: StoreDep, name: str) -> None:
    """Every version."""
    store.delete_dashboard(name)
