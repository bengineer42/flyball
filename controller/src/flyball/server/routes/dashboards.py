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
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from flyball.db import DashboardRow
from flyball.db.errors import DashboardNotFoundError
from flyball.db.store import Store
from flyball.runtime.rig import Rig
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

    cols: Literal[12, 24] = Field(
        default=24, description="24 unless an older document says 12 (client scales x/w on load)."
    )
    row_height: int = Field(default=24, ge=8, description="Pixels per grid row.")


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


class Problem(BaseModel):
    """A widget naming a channel, loop or actuator the live rig does not have.

    Returned alongside the document rather than refusing it (DESIGN-SPEC.md §4.8): a rig with
    one renamed sensor should not lose a 20-widget dashboard. The widget renders the "unbound"
    state instead -- dashed border, this reason, Rebind/Remove in edit mode -- never silently
    dropped (Phoebus's rule: missing data is always visibly indicated).
    """

    model_config = ConfigDict(extra="forbid")

    widget_id: str
    ref: str
    reason: str


class DashboardWithProblems(BaseModel):
    """A stored dashboard plus what it names that this rig does not have."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    rig: str
    body: Any
    created_ns: int
    sha256: str
    problems: list[Problem] = Field(default_factory=list)

    @classmethod
    def of(cls, row: DashboardRow, rig: Rig) -> DashboardWithProblems:
        body = row.body if isinstance(row.body, dict) else {}
        return cls(
            id=row.id,
            name=row.name,
            rig=row.rig,
            body=row.body,
            created_ns=row.created_ns,
            sha256=row.sha256,
            problems=problems_for(body, rig),
        )


def _ref_of(value: Any) -> str | None:
    """A binding value as `source.measurand`.

    Either the string the UI already uses, or the `{source, measurand}` shape the layout
    schema (DESIGN-SPEC.md §5) describes.
    """
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict) and value.get("source") and value.get("measurand"):
        return f"{value['source']}.{value['measurand']}"
    return None


def problems_for(document: dict[str, Any], rig: Rig) -> list[Problem]:
    """Every widget whose binding names a channel, loop or actuator `rig` lacks.

    Bindings live in each widget kind's own `config` (there is no separate `bind` field in this
    build -- see the dashboards-engine report); only the kinds that reference the rig by name are
    checked, matching the widget catalogue (readout/gauge: one channel; chart: several; loop;
    actuator).
    """
    channels = {
        f"{source.name}.{channel.measurand.name}"
        for source in rig.sources
        for channel in source.channels
    }
    problems: list[Problem] = []

    def flag(widget_id: str, ref: str) -> None:
        problems.append(Problem(widget_id=widget_id, ref=ref, reason=f"{ref} is not on this rig"))

    for widget in document.get("widgets") or []:
        if not isinstance(widget, dict):
            continue
        widget_id = str(widget.get("id", ""))
        kind = widget.get("kind")
        config = widget.get("config") or {}
        if not isinstance(config, dict):
            continue
        refs: list[str] = []
        if kind in ("readout", "gauge"):
            ref = _ref_of(config.get("channel"))
            if ref is not None:
                refs.append(ref)
        elif kind == "chart":
            candidates = config.get("channels") or []
            refs.extend(ref for c in candidates if (ref := _ref_of(c)) is not None)
        for ref in refs:
            if ref not in channels:
                flag(widget_id, ref)
        if kind == "loop" and isinstance(config.get("loop"), str) and config["loop"]:
            name = config["loop"]
            if name not in rig.loops:
                flag(widget_id, name)
        if kind == "actuator" and isinstance(config.get("actuator"), str) and config["actuator"]:
            name = config["actuator"]
            if name not in rig.actuators:
                flag(widget_id, name)
    return problems


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
async def read_dashboard(store: StoreDep, rig: RigDep, name: str) -> DashboardWithProblems:
    return DashboardWithProblems.of(store.dashboard(name), rig)


@router.get("/{name}/history")
async def read_dashboard_history(store: StoreDep, name: str) -> list[DashboardRow]:
    """Every version, newest first."""
    return store.dashboard_history(name)


@router.put("/{name}", status_code=201)
async def save_dashboard(
    store: StoreDep, rig: RigDep, name: str, body: Dashboard
) -> DashboardWithProblems:
    """Save a version under `name`; the document's `name` and `rig` are overwritten to match."""
    document = body.model_copy(update={"name": name, "rig": body.rig or rig.name})
    row = store.save_dashboard(
        name, document.rig, document.model_dump(mode="json"), rig.clock.now_ns()
    )
    return DashboardWithProblems.of(row, rig)


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
