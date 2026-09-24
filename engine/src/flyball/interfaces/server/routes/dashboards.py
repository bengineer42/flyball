"""Dashboards: what the UI shows and how, saved per rig.

A dashboard is a document the UI owns -- a grid of widgets, each bound to a
signal address, a controller, a device or nothing -- validated here only in
outline, so the widget catalogue can grow without a server release. The
server keeps every version under a name, like programs; the newest is what
`GET` returns. A rig can ship dashboards as ``dashboards/*.toml``,
``dashboards/*.yaml`` or ``dashboards/*.json`` beside its file (whichever
format the author prefers, per [flyball.foundation.files][]): they are imported
on start, and an edited file becomes a new version.

Documents carry a `schema_version`; an older one is migrated on read (see
[migrate][flyball.interfaces.server.routes.dashboards.migrate]), never refused, and
what is stored is left as saved.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from flyball.foundation.device import Access
from flyball.foundation.files import SUFFIXES, load_document
from flyball.foundation.keys import check_key
from flyball.interfaces.server.deps import RigDep, StoreDep
from flyball.record import DashboardRow
from flyball.record.errors import DashboardNotFoundError
from flyball.record.store import Store
from flyball.rig import Rig

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboards", tags=["dashboards"])

SCHEMA_VERSION = 5
"""The document shape this server writes: bindings are addresses and controller names (2); a
document says whether it is read-only and where its tab sits (3); a program widget's button
cancels, `cancel` (4)."""


class Widget(BaseModel):
    """One tile on the grid. `config` is the widget kind's own business."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str = Field(description="Which widget: `readout`, `chart`, `loop`, `device`, ...")
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

    schema_version: int = SCHEMA_VERSION
    name: str = Field(min_length=1)
    rig: str
    description: str | None = None
    grid: Grid = Field(default_factory=Grid)
    widgets: list[Widget] = Field(default_factory=list)
    readonly: bool = Field(
        default=False,
        description=(
            "Its widgets' write controls render disabled, for everyone. A convenience for a wall "
            "display, not access control: whoever may save the dashboard may clear it. Layout "
            "editing is unaffected."
        ),
    )
    order: float | None = Field(
        default=None,
        description=(
            "Where its tab sits: ascending, then unordered ones newest first. A float so moving "
            "one tab between two others rewrites only that one."
        ),
    )


class Rename(BaseModel):
    name: str = Field(min_length=1)


class Problem(BaseModel):
    """A widget naming a signal, controller or device the live rig does not have.

    Returned alongside the document rather than refusing it (DESIGN-SPEC.md §4.8): a rig with
    one renamed sensor should not lose a 20-widget dashboard. The widget renders the "unbound"
    state instead -- dashed border, this reason, Rebind/Remove in edit mode -- never silently
    dropped (Phoebus's rule: missing data is always visibly indicated).
    """

    model_config = ConfigDict(extra="forbid")

    widget_id: str
    address: str
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
        body = migrate(row.body) if isinstance(row.body, dict) else row.body
        return cls(
            id=row.id,
            name=row.name,
            rig=row.rig,
            body=body,
            created_ns=row.created_ns,
            sha256=row.sha256,
            problems=problems_for(body if isinstance(body, dict) else {}, rig),
        )


def _address_of(value: Any) -> str | None:
    """A version-1 channel binding as an address.

    Either the `source.measurand` string the UI used, or the `{source,
    measurand}` shape the layout schema described; a source was a device and
    a measurand its signal, so the address is the same dotted path.
    """
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict) and value.get("source") and value.get("measurand"):
        return f"{value['source']}.{value['measurand']}"
    return None


def migrate(document: dict[str, Any]) -> dict[str, Any]:
    """A document at any schema version, brought to the current one; a copy.

    Version 1 bound widgets to channels, loops and actuators: a `readout`'s
    or `gauge`'s `channel` becomes `address`, a `chart`'s `channels`
    become `addresses`, a `loop`'s `loop` becomes `controller`, and the
    `actuator` widget becomes a `device` widget bound by `device`. A loop
    was named by its actuator and a controller is named by its output's
    address, so the name is carried as it was; `problems` says if it no
    longer resolves. Version 2 had no `readonly` or `order`: it is writable
    and unordered. Up to version 3 a `program` widget's `interrupt` was what
    is now its `cancel` button. Up to version 4 an `events` widget's `level`
    (`"WARNING"`) was what is now its `severity` (`"warning"`).
    """
    version = document.get("schema_version", 1)
    if not isinstance(version, int) or version >= SCHEMA_VERSION:
        return document
    if version < 2:
        document = _bindings_by_address(document)
    if version < 4:
        document = _program_cancel(document)
    if version < 5:
        document = _events_severity(document)
    return {"readonly": False, "order": None, **document, "schema_version": SCHEMA_VERSION}


def _bindings_by_address(document: dict[str, Any]) -> dict[str, Any]:
    """Version 1 → 2: channel, loop and actuator bindings become addresses and names."""
    widgets: list[Any] = []
    for widget in document.get("widgets") or []:
        if not isinstance(widget, dict) or not isinstance(widget.get("config"), dict):
            widgets.append(widget)
            continue
        kind = widget.get("kind")
        config = dict(widget["config"])
        if kind in ("readout", "gauge") and "channel" in config:
            if (address := _address_of(config.pop("channel"))) is not None:
                config["address"] = address
        elif kind == "chart" and "channels" in config:
            channels = config.pop("channels")
            config["addresses"] = (
                [a for c in channels if (a := _address_of(c)) is not None]
                if isinstance(channels, list)
                else []
            )
        elif kind == "loop" and "loop" in config:
            config["controller"] = config.pop("loop")
        elif kind == "actuator":
            kind = "device"
            if "actuator" in config:
                config["device"] = config.pop("actuator")
        widgets.append({**widget, "kind": kind, "config": config})
    return {**document, "widgets": widgets}


def _program_cancel(document: dict[str, Any]) -> dict[str, Any]:
    """Version 3 → 4: a `program` widget's `interrupt` button is its `cancel` button."""
    widgets: list[Any] = []
    for widget in document.get("widgets") or []:
        config = widget.get("config") if isinstance(widget, dict) else None
        if widget.get("kind") == "program" and isinstance(config, dict) and "interrupt" in config:
            config = {("cancel" if k == "interrupt" else k): v for k, v in config.items()}
            widget = {**widget, "config": config}
        widgets.append(widget)
    return {**document, "widgets": widgets}


def _events_severity(document: dict[str, Any]) -> dict[str, Any]:
    """Version 4 → 5: an `events` widget's `level` is its `severity`, a lowercase string."""
    widgets: list[Any] = []
    for widget in document.get("widgets") or []:
        config = widget.get("config") if isinstance(widget, dict) else None
        if widget.get("kind") == "events" and isinstance(config, dict) and "level" in config:
            config = {
                ("severity" if k == "level" else k): (
                    v.lower() if k == "level" and isinstance(v, str) else v
                )
                for k, v in config.items()
            }
            widget = {**widget, "config": config}
        widgets.append(widget)
    return {**document, "widgets": widgets}


def problems_for(document: dict[str, Any], rig: Rig) -> list[Problem]:
    """Every widget whose binding names a signal, controller or device `rig` lacks.

    Bindings live in each widget kind's own `config`; only the kinds that
    reference the rig by name are checked, matching the widget catalogue
    (readout/gauge: one address; chart: several; loop: a controller;
    device: a device). A readout binds to what publishes.
    """
    published = {
        signal.address
        for device in rig.devices.values()
        for signal in device.signals.values()
        if Access.P in signal.access
    }
    problems: list[Problem] = []

    def flag(widget_id: str, address: str) -> None:
        problems.append(
            Problem(widget_id=widget_id, address=address, reason=f"{address} is not on this rig")
        )

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
            if isinstance(address := config.get("address"), str) and address:
                refs.append(address)
        elif kind == "chart":
            candidates = config.get("addresses") or []
            refs.extend(a for a in candidates if isinstance(a, str) and a)
        for ref in refs:
            if ref not in published:
                flag(widget_id, ref)
        name = config.get("controller") if kind == "loop" else config.get("device")
        known = rig.controllers if kind == "loop" else rig.devices
        if isinstance(name, str) and name and name not in known:
            flag(widget_id, name)
    return problems


def _migrated(row: DashboardRow) -> DashboardRow:
    return replace(row, body=migrate(row.body)) if isinstance(row.body, dict) else row


def import_directory(store: Store, directory: Path, rig: str, now_ns: int) -> list[DashboardRow]:
    """Bring every dashboard file in `directory` into the store, for `rig`.

    Any of `SUFFIXES` (`.toml`, `.yaml`/`.yml`, `.json`) is read, by suffix,
    same as a rig file. The file's stem is the name; the document's own
    `name`/`rig` are set from it. An unchanged file is left alone; an
    invalid one is skipped, not fatal.
    """
    imported: list[DashboardRow] = []
    paths = sorted(p for suffix in SUFFIXES for p in directory.glob(f"*{suffix}"))
    for path in paths:
        if not path.is_file():
            continue
        try:
            name = check_key(path.stem, "dashboard name")
            document = Dashboard.model_validate({
                **load_document(path),
                "name": name,
                "rig": rig,
            })
        except (ValueError, TypeError) as e:
            log.warning("dashboard %s skipped: %s", path, e)
            continue
        body = document.model_dump(mode="json")
        digest = hashlib.sha256(json.dumps(body, separators=(",", ":")).encode()).hexdigest()
        try:
            if store.dashboard(name).sha256 == digest:
                continue
        except DashboardNotFoundError:
            pass
        imported.append(store.save_dashboard(name, rig, body, now_ns))
    return imported


@router.get("/schema")
async def read_dashboard_schema() -> dict[str, Any]:
    """JSON Schema of the document, for an editor or an import check."""
    return Dashboard.model_json_schema()


@router.get("/widgets")
async def read_widget_catalogue() -> dict[str, Any]:
    """Every widget kind and its `config` schema, for a client writing a document by hand.

    The UI owns the kinds; `widgets.json` beside the server package is a copy
    of its registry with the rig-dependent pickers reduced to `x-binding`.
    """
    path = Path(__file__).parent.parent / "widgets.json"
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@router.get("")
def list_dashboards(
    store: StoreDep,
    rig: RigDep,
    every: bool = Query(False, description="Every rig's, not only this one's."),
) -> list[DashboardRow]:
    """The newest version of each dashboard, by name; this rig's unless ``every``."""
    return [_migrated(row) for row in store.dashboards(None if every else rig.name)]


@router.get("/{name}")
def read_dashboard(store: StoreDep, rig: RigDep, name: str) -> DashboardWithProblems:
    return DashboardWithProblems.of(store.dashboard(name), rig)


@router.get("/{name}/history")
def read_dashboard_history(store: StoreDep, name: str) -> list[DashboardRow]:
    """Every version, newest first."""
    return [_migrated(row) for row in store.dashboard_history(name)]


@router.put("/{name}", status_code=201)
def save_dashboard(
    store: StoreDep, rig: RigDep, name: str, body: Dashboard
) -> DashboardWithProblems:
    """Save a version under `name`; the document's `name` and `rig` are overwritten to match."""
    name = check_key(name, "dashboard name")
    document = body.model_copy(update={"name": name, "rig": body.rig or rig.name})
    row = store.save_dashboard(
        name, document.rig, document.model_dump(mode="json"), rig.clock.now_ns()
    )
    return DashboardWithProblems.of(row, rig)


@router.post("/{name}/rename")
def rename_dashboard(store: StoreDep, name: str, body: Rename) -> list[DashboardRow]:
    """Move every version under a new name. 409 if taken."""
    new_name = check_key(body.name, "dashboard name")
    rows = store.rename_dashboard(name, new_name)
    # The document names itself too: keep the newest in step with its key.
    newest = rows[0]
    if isinstance(newest.body, dict) and newest.body.get("name") != new_name:
        store.save_dashboard(
            new_name, newest.rig, {**newest.body, "name": new_name}, newest.created_ns + 1
        )
        rows = store.dashboard_history(new_name)
    return rows


@router.delete("/{name}", status_code=204)
def delete_dashboard(store: StoreDep, name: str) -> None:
    """Every version."""
    store.delete_dashboard(name)
