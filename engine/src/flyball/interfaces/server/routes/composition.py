"""Changing the rig: links, devices, whole documents, a version restored; saving it to a file.

The rig file is one way to populate a rig; these routes are the other. A link or a device is
posted in the file's own form (the `links:` entry, the device envelope), a whole document may
be posted at once, and a recorded version may be restored. None of it is applied to the
running rig (D-051): every change is validated, saved as a new rig version (the head) where
the next start finds it (`flyball.runtime.edits`), the rig is stopped, and the runner starts
again from that version, passive: every controller in manual, each driver at its build
values. The route answers 202 before the restart. `save` writes the running rig out: by
default what changed since the files were loaded, to the overlay the runner loads next
start; to a path of your choosing, the whole rig.
"""

from __future__ import annotations

import copy
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from flyball.foundation.config import Config
from flyball.foundation.device import DeviceEntry
from flyball.foundation.files import SUFFIXES, dumps_without_none, load_document
from flyball.interfaces.server.deps import (
    RigDep,
    compose_allowed,
    current_programmer,
    current_runner,
    current_stopper,
    get_catalog,
    get_store,
    save_allowed,
)
from flyball.interfaces.server.routes.stop import actor
from flyball.record import RigVersionRow
from flyball.rig import Rig
from flyball.runtime import edits
from flyball.runtime.config import (
    RigConfig,
    document_of,
    is_simulated,
    registered,
    saved_overlay_path,
)
from flyball.runtime.overlay import delta, resolve_layers

router = APIRouter(prefix="/api", tags=["composition"])

log = logging.getLogger("flyball.server")


def _composable(rig: Rig) -> None:
    """Changing a rig with real hardware on it is opt-in: `--compose`.

    A simulated rig (every link a sim or a fake) and a bare one (no links
    yet) may always be changed; anything else answers 409 without the flag.
    """
    if compose_allowed() or not rig.link_entries or is_simulated(rig.link_entries):
        return
    raise HTTPException(
        status_code=409,
        detail="Composition is off on a hardware rig: start the runner with --compose",
    )


def _link_adapter() -> TypeAdapter[Any]:
    return TypeAdapter(Config.union(*registered("link", get_catalog())))


def _validated[T](adapter: TypeAdapter[T], body: Any) -> T:
    try:
        return adapter.validate_python(body)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


# region The edit: validate, save, stop, restart


class EditOut(BaseModel):
    """What a rig edit did: saved as a version, the rig stopped, the runner restarting."""

    version: int
    """The edit's rig version, now the head: what the runner starts again from."""
    previous: int | None
    """The head before it; what a start that cannot build the edit goes back to."""
    reason: str
    """The version's reason: `edited: added device probe`, `restored from 3`."""
    saved: str | None
    """The overlay the edit was saved to (`<rig file>.d/added.<suffix>`); None for a bare or
    resumed rig, whose edits live in the store alone."""
    restarting: bool = True
    stop: dict[str, Any] | None
    """The stop's report (`POST /api/rig/stop`'s shape); None if the stop failed (logged)."""
    detail: str


_editing = threading.Lock()
"""One edit at a time: each is made on the rig as the one before it left it."""


def _edit(
    rig: Rig,
    request: Request,
    change: Callable[[dict[str, Any]], str],
    base: int | None,
    force: bool,
    restored: int | None = None,
) -> EditOut:
    """Apply `change` to the rig's document, then check, save, stop and restart (D-051).

    `change` edits the document in place and returns what it did (`added device probe`),
    or raises an HTTPException, before anything is written. Every refusal leaves the rig,
    the store and the files as they were.
    """
    runner = current_runner()
    if runner is None:
        raise HTTPException(
            status_code=409,
            detail="A rig edit restarts the runner, and this server has none"
            " (not served by flyball-runner)",
        )
    _composable(rig)
    with _editing:
        if runner.restarting:  # the process answering is the old one: its rig is going
            raise HTTPException(
                status_code=409, detail="The runner is restarting: edit again once it is back"
            )
        store = get_store()
        head = store.head_rig_version()
        previous = None if head is None else head.id
        if base is not None and base != previous:
            raise HTTPException(
                status_code=409,
                detail=f"The rig is at version {previous}, not {base}: read it again and"
                " make the edit on that",
            )
        programmer = current_programmer()
        if programmer is not None and programmer.state.running and not force:
            raise HTTPException(
                status_code=409,
                detail="A program is running: a rig edit stops the rig and cancels it."
                " Pass force=true to do so",
            )
        running = rig.document()
        document = copy.deepcopy(running)
        what = change(document)
        _refers(document, rig)
        try:
            plan = edits.plan(runner.origin, document)
        except edits.NotSaveable as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except (ValidationError, ValueError, TypeError) as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        if plan.document == document_of(RigConfig.model_validate(running)):
            raise HTTPException(
                status_code=409,
                detail="Nothing to change: the rig already is that; nothing was stopped",
            )
        reason = f"restored from {restored}" if restored is not None else f"edited: {what}"
        try:
            edits.write(plan)
        except OSError as e:
            raise HTTPException(
                status_code=409, detail=f"The edit could not be saved to {plan.path}: {e}"
            ) from e
        try:
            files = [str(p) for p in rig.files]
            if plan.path is not None and plan.overlay and str(plan.path) not in files:
                files.append(str(plan.path))
            row = store.save_rig_version(rig.clock.now_ns(), reason, plan.document, files)
        except Exception:
            edits.roll_back(runner.origin)
            raise
        log.warning("rig edit (%s): version %d; stopping the rig and restarting", reason, row.id)
        report = _stop(rig, request, f"rig edit: {reason}; restarting at version {row.id}")
        runner.restart_for_edit(row.id, previous, record=rig.recording is not None)
    return EditOut(
        version=row.id,
        previous=previous,
        reason=reason,
        saved=None if plan.path is None else str(plan.path),
        stop=report,
        detail=f"The rig is restarting at version {row.id}: outputs went to their stop,"
        " a running program was cancelled, and controllers come back in manual;"
        " a recording goes on in a new session",
    )


def _stop(rig: Rig, request: Request, reason: str) -> dict[str, Any] | None:
    """The installed stopper's stop, before the restart; a failure is logged, not raised."""
    stopper = current_stopper()
    if stopper is None:
        return None
    try:
        return stopper.stop(actor(request), reason).as_dict()
    except Exception:
        log.exception("the stop before a rig edit's restart failed; restarting anyway")
        return None


def _refers(document: dict[str, Any], rig: Rig) -> None:
    """Every address an input or a controller names is on a device the edited rig has.

    Checked without building: the device part only (the signal is the build's to find).
    A device built in code rather than from an entry (not in the document) counts.
    """
    devices = set(document.get("devices", {})) | (set(rig.devices) - set(rig.entries))

    def check(address: Any, where: str) -> None:
        if isinstance(address, str) and address.split(".", 1)[0] not in devices:
            raise HTTPException(
                status_code=404, detail=f"{where}: {address!r} is not on a device of the rig"
            )

    for name, entry in document.get("devices", {}).items():
        for input_name, source in (entry.get("inputs") or {}).items():
            check(source, f"device {name!r} input {input_name!r}")
    for output, controller in document.get("controllers", {}).items():
        check(output, f"controller {output!r}")
        check(controller.get("measured"), f"controller {output!r} measured")


# endregion

# region Links


class NewLink(BaseModel):
    """A link as the file writes one, with its name: `{name, type, ...}`."""

    model_config = ConfigDict(extra="allow")

    name: str


@router.post("/links", status_code=202)
def add_link(
    rig: RigDep, request: Request, body: NewLink, base: int | None = None, force: bool = False
) -> EditOut:
    """Add a link and restart the rig with it; 409 if the name is taken, 422 for a bad config."""
    entry = {k: v for k, v in body.model_dump().items() if k != "name"}
    _validated(_link_adapter(), entry)

    def change(document: dict[str, Any]) -> str:
        if body.name in document["links"] or body.name in rig.links:
            raise HTTPException(status_code=409, detail=f"Link {body.name!r} already exists")
        document["links"][body.name] = entry
        return f"added link {body.name}"

    return _edit(rig, request, change, base, force)


@router.delete("/links/{name}", status_code=202)
def remove_link(
    rig: RigDep, request: Request, name: str, base: int | None = None, force: bool = False
) -> EditOut:
    """Remove a link no device is built on, and restart the rig without it; 409 while one is."""

    def change(document: dict[str, Any]) -> str:
        if name not in document["links"]:
            raise HTTPException(status_code=404, detail=f"Link {name!r} not found")
        users = [d for d, e in document["devices"].items() if e.get("link") == name]
        if users:
            raise HTTPException(
                status_code=409, detail=f"Link {name!r} is used by {', '.join(users)}"
            )
        del document["links"][name]
        return f"removed link {name}"

    return _edit(rig, request, change, base, force)


# endregion

# region Devices


@router.post("/devices", status_code=202)
def add_device(
    rig: RigDep,
    request: Request,
    body: dict[str, Any],
    base: int | None = None,
    force: bool = False,
) -> EditOut:
    """Add a device and restart the rig with it: built, bound, polled, recorded from then on.

    The body is the file's device entry with its `name` beside it:
    `{name, driver, label, poll_s, signals, inputs, ...the driver's fields}`.
    409 for a name in use, 404 for an unknown link or an input address on no device of
    the rig, 422 for an unknown driver or a config it refuses. A config that validates but
    will not build is caught at the restart, which goes back to the version before.
    """
    name = body.get("name")
    if not isinstance(name, str) or not name:
        raise HTTPException(status_code=422, detail="a device needs a name")
    entry = {k: v for k, v in body.items() if k != "name"}
    _validated(TypeAdapter(DeviceEntry), entry)

    def change(document: dict[str, Any]) -> str:
        if name in document["devices"] or name in rig.devices:
            raise HTTPException(status_code=409, detail=f"Device {name!r} already exists")
        link = entry.get("link")
        if isinstance(link, str) and link not in document["links"]:
            raise HTTPException(status_code=404, detail=f"Link {link!r} not found")
        document["devices"][name] = entry
        return f"added device {name}"

    return _edit(rig, request, change, base, force)


@router.delete("/devices/{name}", status_code=202)
def remove_device(
    rig: RigDep, request: Request, name: str, base: int | None = None, force: bool = False
) -> EditOut:
    """Remove a device and restart the rig without it; the controllers on it go with it.

    404 if there is none; 409 while another device's input is bound to it.
    """

    def change(document: dict[str, Any]) -> str:
        if name not in document["devices"]:
            raise HTTPException(status_code=404, detail=f"Device {name!r} not found")
        del document["devices"][name]
        bound = [
            f"{other}.{input_name}"
            for other, entry in document["devices"].items()
            for input_name, source in (entry.get("inputs") or {}).items()
            if isinstance(source, str) and source.split(".", 1)[0] == name
        ]
        if bound:
            raise HTTPException(
                status_code=409,
                detail=f"Device {name!r} is an input of {', '.join(bound)}: bind those"
                " elsewhere first",
            )
        document["controllers"] = {
            output: c
            for output, c in document["controllers"].items()
            if name not in (output.split(".", 1)[0], str(c.get("measured", "")).split(".", 1)[0])
        }
        return f"removed device {name}"

    return _edit(rig, request, change, base, force)


# endregion

# region The whole document


@router.post("/rig", status_code=202)
def add_document(
    rig: RigDep,
    request: Request,
    document: dict[str, Any],
    base: int | None = None,
    force: bool = False,
) -> EditOut:
    """Add a rig document's links, devices and controllers, and restart the rig with them.

    The same shape as a rig file (`links`, `devices`, `controllers`; a `name`, `clock` or
    `board` is ignored). Validated whole with the rig it joins before anything is saved;
    a name the rig already has is 409.
    """
    added = {k: document.get(k) or {} for k in ("links", "devices", "controllers")}
    try:
        RigConfig.model_validate(added)
    except (ValidationError, ValueError, TypeError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    def change(current: dict[str, Any]) -> str:
        for section, entries in added.items():
            taken = sorted(set(entries) & set(current[section]))
            if taken:
                raise HTTPException(
                    status_code=409,
                    detail=f"{section} {', '.join(taken)} already on the rig",
                )
            current[section].update(copy.deepcopy(entries))
        counts = ", ".join(f"{len(v)} {k}" for k, v in added.items() if v)
        return f"added a document ({counts or 'nothing'})"

    return _edit(rig, request, change, base, force)


@router.get("/rig/document")
def read_document(rig: RigDep) -> dict[str, Any]:
    """The running rig as a rig file would build it: links, devices, controllers as they are now."""
    return rig.document()


@router.get("/rig/changes")
def read_changes(rig: RigDep) -> dict[str, Any]:
    """What differs between the running rig and the files it was loaded from, as an overlay.

    A key added or changed appears with its value; one removed appears as
    null (what an overlay writes to delete). Empty when nothing has changed:
    an edit restarts the rig, so only a controller attached or removed since
    the start shows here.
    """
    return _changes(rig)


def _changes(rig: Rig) -> dict[str, Any]:
    """What differs from the rig as this run started; nothing, if it was not built from a config."""
    return {} if rig.loaded is None else delta(rig.loaded, rig.document())


# endregion

# region Versions


class VersionOut(BaseModel):
    id: int
    time_ns: int
    reason: str
    files: list[str]
    parent: int | None
    """The version this was made from: the head at the time."""
    head: bool
    """Whether this is the head: what the running rig is at, or is restarting to."""

    @classmethod
    def of(cls, row: RigVersionRow, head: int | None) -> VersionOut:
        return cls(
            id=row.id,
            time_ns=row.time_ns,
            reason=row.reason,
            files=row.files,
            parent=row.parent,
            head=row.id == head,
        )


def _head() -> int | None:
    head = get_store().head_rig_version()
    return None if head is None else head.id


class VersionDocumentOut(VersionOut):
    document: dict[str, Any]


@router.get("/rig/versions")
def read_versions(limit: int | None = None) -> list[VersionOut]:
    """Every version of the rig this store has seen, newest first: when, and why it changed.

    Each names its `parent` and whether it is the `head` -- the one the
    running rig is at.
    """
    head = _head()
    return [VersionOut.of(row, head) for row in get_store().rig_versions(limit)]


@router.get("/rig/versions/{version_id}")
def read_version(version_id: int) -> VersionDocumentOut:
    row = get_store().rig_version(version_id)
    return VersionDocumentOut(**VersionOut.of(row, _head()).model_dump(), document=row.document)


@router.post("/rig/versions/{version_id}/restore", status_code=202)
def restore_version(
    rig: RigDep, request: Request, version_id: int, base: int | None = None, force: bool = False
) -> EditOut:
    """Make the rig that version again: saved as a new version (`restored from N`), restarted.

    The whole rig is the version's -- links, devices, controllers -- built fresh at the
    restart, every driver at its build values. 404 for no such version.
    """
    target = get_store().rig_version(version_id).document

    def change(document: dict[str, Any]) -> str:
        document.clear()
        document.update(copy.deepcopy(target))
        return f"restored from {version_id}"

    return _edit(rig, request, change, base, force, restored=version_id)


# endregion

# region Saving


class SaveIn(BaseModel):
    path: str | None = None
    """Where to write. None: the runner's own overlay beside the first rig file, holding what
    changed since the files were loaded (an edit already saved itself there: this adds a
    controller attached or removed since). A path: the whole rig, flattened -- only on a
    runner started with `--allow-save`."""
    overwrite: bool = False
    """Allow `path` to be one of the files the rig was loaded from. The rig is then all in
    that file, so the saved overlay is cleared (kept as `added.<suffix>.prev`)."""


@router.post("/rig/save")
def save(rig: RigDep, body: SaveIn | None = None) -> dict[str, Any]:
    """Write the running rig out; see `SaveIn`.

    Returns the path, the document and whether it was written: the default overlay is left
    alone when it already says the same.
    """
    body = body or SaveIn()
    if body.path is None:
        if not rig.files:
            raise HTTPException(
                status_code=409, detail="The rig was not started from a file: say where to save"
            )
        target = saved_overlay_path(rig.files[0])
        # The overlay this run loaded is already in `rig.loaded`, so `_changes` holds only this
        # run's changes: the file gets both, or a second run's save would drop the first's.
        document = merge_overlays(rig.saved_overlay, _changes(rig))
        existing = load_document(target) if target.exists() else None
        if document == (existing or {}):
            return {"path": str(target), "document": document, "written": False}
        target.parent.mkdir(exist_ok=True)
    else:
        if not save_allowed():
            raise HTTPException(
                status_code=409,
                detail="Saving to a path needs the runner started with --allow-save"
                " (runner.allow_save); with no path, the overlay is always written",
            )
        target = Path(body.path)
        if target.suffix.lower() not in SUFFIXES:
            raise HTTPException(
                status_code=422, detail=f"{target}: use one of {', '.join(SUFFIXES)}"
            )
        if any(target.resolve() == f.resolve() for f in rig.files) and not body.overwrite:
            raise HTTPException(
                status_code=409,
                detail=f"{target} is a file the rig was loaded from; overwrite: true to flatten it",
            )
        document = rig.document()
    text = dumps_without_none({**document, **_runner_section(target)}, target.suffix)
    partial = target.with_name(target.name + ".tmp")
    partial.write_text(text)
    os.replace(partial, target)
    if (
        body.path is not None
        and rig.files
        and any(target.resolve() == f.resolve() for f in rig.files)
    ):
        _clear_overlay(saved_overlay_path(rig.files[0]))
    return {"path": str(target), "document": document, "written": True}


def _clear_overlay(overlay: Path) -> None:
    """The saved overlay, flattened into a rig file, moved aside to `.prev`: it would repeat it."""
    if overlay.exists():
        os.replace(overlay, overlay.with_name(overlay.name + ".prev"))


def merge_overlays(first: dict[str, Any], then: dict[str, Any]) -> dict[str, Any]:
    """One overlay that does what applying `first`, then `then`, does.

    Mappings merge key by key; anything else in `then` replaces what `first` had, a `None` (a
    deletion) included -- kept, not applied, so it still deletes from the files beneath.
    """
    out = dict(first)
    for key, value in then.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge_overlays(out[key], value)
        else:
            out[key] = value
    return out


def _runner_section(target: Path) -> dict[str, Any]:
    """`{"runner": ...}` from the file about to be overwritten, its `extends` resolved; else `{}`.

    The rig's document has no runner section -- how the process serves is not part of the
    rig -- so a save over a file would otherwise drop its `runner.auth`, and the next start
    from that file would be open. Written to the file, never returned: it may hold secrets.
    A file whose section cannot be read is not overwritten.
    """
    if not target.exists():
        return {}
    try:
        existing, _ = resolve_layers([target])
    except Exception as e:  # a broken file: saving over it could drop a password unseen
        raise HTTPException(
            status_code=409,
            detail=f"{target} exists and its runner section cannot be read ({e}); not"
            " overwriting it: save to a new path, or fix or remove the file",
        ) from e
    section = existing.get("runner")
    return {} if section is None else {"runner": section}


# endregion


__all__ = ["router"]
