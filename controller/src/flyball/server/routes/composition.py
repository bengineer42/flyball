"""Building a rig up while it runs: links, devices, whole documents; versions; saving to a file.

The rig file is one way to populate a rig; these routes are the other. A
link or a device is posted in the file's own form (the `links:` entry, the
device envelope), a whole document may be posted at once, and every change
is a version in the store (`rig_version`): the rendered rig, self-contained,
with a reason. `save` writes the running rig out -- by default what changed
since the files were loaded, to an overlay the daemon picks up next start;
to a path of your choosing, the whole rig.
"""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from flyball.core.config import Config
from flyball.core.device import DeviceEntry
from flyball.core.errors import ConflictError
from flyball.core.files import SUFFIXES
from flyball.db import RigVersionRow
from flyball.runtime.config import RigConfig, canonical, is_simulated, registered
from flyball.runtime.rig import Rig
from flyball.runtime.simulation import dumps
from flyball.server.deps import RigDep, compose_allowed, get_store
from flyball.server.routes.devices import device_out
from flyball.server.schemas import DeviceOut

router = APIRouter(prefix="/api", tags=["composition"])


def _composable(rig: Rig) -> None:
    """Building up a rig with real hardware on it is opt-in: `--compose`.

    A simulated rig (every link a sim or a fake) and a bare one (no links
    yet) may always be built up; anything else answers 409 without the flag.
    """
    if compose_allowed() or not rig.link_entries or is_simulated(rig.link_entries):
        return
    raise HTTPException(
        status_code=409,
        detail="Composition is off on a hardware rig: start the daemon with --compose",
    )


def _link_adapter() -> TypeAdapter[Any]:
    return TypeAdapter(Config.union(*registered("link")))


def _validated[T](adapter: TypeAdapter[T], body: Any) -> T:
    try:
        return adapter.validate_python(body)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


# region Links


class NewLink(BaseModel):
    """A link as the file writes one, with its name: `{name, tag, ...}`."""

    model_config = ConfigDict(extra="allow")

    name: str


@router.post("/links", status_code=201)
def add_link(rig: RigDep, body: NewLink) -> dict[str, Any]:
    """Build a link and hold it under `name`; 409 if the name is taken, 422 for a bad config."""
    entry = {k: v for k, v in body.model_dump().items() if k != "name"}
    config = _validated(_link_adapter(), entry)
    _composable(rig)
    with rig.lock:
        rig.add_link(body.name, config)
    return {"name": body.name, **rig.document()["links"][body.name]}


@router.delete("/links/{name}", status_code=204)
def remove_link(rig: RigDep, name: str) -> None:
    """Drop a link no device is built on; 409 while one is."""
    _composable(rig)
    rig.remove_link(name)


# endregion

# region Devices


@router.post("/devices", status_code=201)
def add_device(rig: RigDep, body: dict[str, Any]) -> DeviceOut:
    """Build a device on the rig's links and put it on the rig: bound, polling, recorded.

    The body is the file's device entry with its `name` beside it:
    `{name, driver, config | flat settings, label, poll_s, signals, bound}`.
    409 for a name in use, 404 for an unknown link or a bound address that
    does not resolve, 422 for an unknown driver or a config it refuses.
    """
    name = body.get("name")
    if not isinstance(name, str) or not name:
        raise HTTPException(status_code=422, detail="a device needs a name")
    entry = _validated(TypeAdapter(DeviceEntry), {k: v for k, v in body.items() if k != "name"})
    _composable(rig)
    try:
        device = rig.add_entry(name, entry)
    except ValueError as e:  # an unregistered driver, a config the driver refuses
        raise HTTPException(status_code=422, detail=str(e)) from e
    return device_out(rig, device)


@router.delete("/devices/{name}", status_code=204)
def remove_device(rig: RigDep, name: str) -> None:
    """Take a device off the rig with everything that hung off it; 404 if there is none."""
    _composable(rig)
    rig.remove_device(name)


# endregion

# region The whole document


@router.post("/rig", status_code=201)
def add_document(rig: RigDep, document: dict[str, Any]) -> dict[str, Any]:
    """Add a rig document's links, devices and controllers to the running rig, in that order.

    The same shape as a rig file (`links`, `devices`, `controllers`; a
    `name`, `clock` or `board` is ignored). Validated whole before anything
    is built; a failure part-way leaves what was built before it.
    """
    try:
        config = RigConfig.model_validate({
            k: v for k, v in document.items() if k in ("links", "devices", "controllers")
        })
    except (ValidationError, ValueError, TypeError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    _composable(rig)
    with rig.lock:
        for name, link in config.links.items():
            rig.add_link(name, link)
        for name, entry in config.devices.items():
            rig.add_entry(name, entry)
        for target, controller in config.controllers.items():
            rig.attach_controller(
                _signal(rig, target),
                _signal(rig, controller.signal),
                law=controller.law,
                feedforward=controller.feedforward,
                default=controller.default,
                min_period_s=controller.min_period_s,
            )
    return rig.document()


def _signal(rig: Rig, address: str) -> Any:
    from flyball.core.signal import Signal

    found = rig.resolve(address)
    if not isinstance(found, Signal):
        raise ConflictError(f"'{address}' is a namespace, not a signal")
    return found


@router.get("/rig/document")
def read_document(rig: RigDep) -> dict[str, Any]:
    """The running rig as a rig file would build it: links, devices, controllers as they are now."""
    return rig.document()


@router.get("/rig/changes")
def read_changes(rig: RigDep) -> dict[str, Any]:
    """What differs between the running rig and the files it was loaded from, as an overlay.

    A key added or changed appears with its value; one removed appears as
    null (what an overlay writes to delete). Empty when nothing has changed.
    """
    return _changes(rig)


def _changes(rig: Rig) -> dict[str, Any]:
    """What differs from the rig as this run started; nothing, if it was not built from a config."""
    return {} if rig.loaded is None else _delta(rig.loaded, rig.document())


def _delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """The overlay that turns `before` into `after`: `merge(before, delta) == after`."""
    out: dict[str, Any] = {}
    for key in set(before) | set(after):
        if key not in after:
            out[key] = None
        elif key not in before:
            out[key] = after[key]
        elif isinstance(before[key], dict) and isinstance(after[key], dict):
            if (inner := _delta(before[key], after[key])) != {}:
                out[key] = inner
        elif before[key] != after[key]:
            out[key] = after[key]
    return out


# endregion

# region Versions


class VersionOut(BaseModel):
    id: int
    time_ns: int
    reason: str
    files: list[str]

    @classmethod
    def of(cls, row: RigVersionRow) -> VersionOut:
        return cls(id=row.id, time_ns=row.time_ns, reason=row.reason, files=row.files)


class VersionDocumentOut(VersionOut):
    document: dict[str, Any]


@router.get("/rig/versions")
def read_versions(limit: int | None = None) -> list[VersionOut]:
    """Every version of the rig this store has seen, newest first: when, and why it changed."""
    return [VersionOut.of(row) for row in get_store().rig_versions(limit)]


@router.get("/rig/versions/{version_id}")
def read_version(version_id: int) -> VersionDocumentOut:
    row = get_store().rig_version(version_id)
    return VersionDocumentOut(**VersionOut.of(row).model_dump(), document=row.document)


@router.post("/rig/versions/{version_id}/restore")
def restore_version(rig: RigDep, version_id: int) -> dict[str, Any]:
    """Make the running rig that version again: links, devices and controllers rebuilt.

    Everything not in the version is removed; everything in it that is not
    running is added; a device whose entry differs is rebuilt. Controllers
    are re-attached from the version. Records a version of its own.
    """
    target = get_store().rig_version(version_id).document
    _composable(rig)
    with rig.lock:
        hook, rig.on_change = rig.on_change, None  # one version for the whole restore
        try:
            _apply(rig, target)
        finally:
            rig.on_change = hook
        rig._changed(f"restored {version_id}")
    return rig.document()


def _apply(rig: Rig, target: dict[str, Any]) -> None:
    """Make the rig match `target`: remove what is absent or changed, add what is missing."""
    config = RigConfig.model_validate(target)
    wanted, current = canonical(config), rig.document()
    for name in list(rig.controllers):
        rig.detach_controller(name)
    for name in list(rig.devices):
        if wanted.get("devices", {}).get(name) != current["devices"].get(name):
            rig.remove_device(name)
    for name in list(rig.links):
        if wanted.get("links", {}).get(name) != current["links"].get(name):
            with suppress(ConflictError):  # still built on by a device that stays: kept
                rig.remove_link(name)
    for name, link in config.links.items():
        if name not in rig.links:
            rig.add_link(name, link)
    for name, entry in config.devices.items():
        if name not in rig.devices:
            rig.add_entry(name, entry)
    for target_address, controller in config.controllers.items():
        rig.attach_controller(
            _signal(rig, target_address),
            _signal(rig, controller.signal),
            law=controller.law,
            feedforward=controller.feedforward,
            default=controller.default,
            min_period_s=controller.min_period_s,
        )


# endregion

# region Saving


class SaveIn(BaseModel):
    path: str | None = None
    """Where to write. None: the daemon's own overlay beside the first rig file, holding what
    changed since the files were loaded. A path: the whole rig, flattened."""
    overwrite: bool = False
    """Allow `path` to be one of the files the rig was loaded from."""


@router.post("/rig/save")
def save(rig: RigDep, body: SaveIn | None = None) -> dict[str, Any]:
    """Write the running rig out; see `SaveIn`. Returns the path and what was written."""
    body = body or SaveIn()
    if body.path is None:
        if not rig.files:
            raise HTTPException(
                status_code=409, detail="The rig was not started from a file: say where to save"
            )
        first = rig.files[0]
        target = first.with_name(first.name + ".d") / f"added{first.suffix}"
        document = _changes(rig)
        target.parent.mkdir(exist_ok=True)
    else:
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
    text = dumps(document, target.suffix)
    partial = target.with_name(target.name + ".tmp")
    partial.write_text(text)
    os.replace(partial, target)
    return {"path": str(target), "document": document}


# endregion


__all__ = ["router"]
