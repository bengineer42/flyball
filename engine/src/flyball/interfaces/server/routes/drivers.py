"""What this runner can build with, and the hardware it can see: for writing a rig or a driver.

`/api/drivers` lists every registered driver with its config schema;
`/api/drivers/reload` re-imports the runner's `drivers/` directory so a
driver being written appears without a restart; `/api/probe` reports the
board's buses and chips (flyball-linux, on a Linux box); a text link takes
one query, for finding out what an instrument answers.
"""

from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from flyball.foundation.config import Config
from flyball.foundation.device import DriverConfig
from flyball.foundation.errors import ConflictError, NotFoundError
from flyball.interfaces.server.deps import RigDep, current_drivers_dir
from flyball.runtime.drivers import load_drivers

router = APIRouter(prefix="/api", tags=["drivers"])


@router.get("/drivers")
def read_drivers() -> dict[str, Any]:
    """Every registered config, by tag: its role, module, description and config schema."""
    out: dict[str, Any] = {}
    for tag, config in sorted(Config.registry.items()):
        entry: dict[str, Any] = {
            "role": "driver" if issubclass(config, DriverConfig) else "link",
            "module": config.__module__,
            "description": inspect.getdoc(config),
        }
        try:
            entry["schema"] = config.model_json_schema()
        except Exception as e:  # a schema pydantic cannot build: say so, keep the rest
            entry["schema_error"] = f"{type(e).__name__}: {e}"
        out[tag] = entry
    return out


@router.post("/drivers/reload")
def reload_drivers() -> dict[str, Any]:
    """Import every `.py` in the runner's drivers directory again; what each registered, or why not.

    404 when the runner has no drivers directory (`--drivers`, or `drivers/`
    beside the first rig file).
    """
    directory = current_drivers_dir()
    if directory is None:
        raise HTTPException(status_code=404, detail="This runner has no drivers directory")
    report = load_drivers(directory)
    return {
        "directory": report.directory,
        "registered": report.registered,
        "errors": report.errors,
    }


@router.get("/probe")
def probe(scan: bool = True) -> dict[str, str]:
    """The board this runner runs on: its buses, GPIO chips and, with `scan`, I²C addresses.

    404 where flyball-linux is not installed.
    """
    try:
        from flyball_linux.probe import report  # pyright: ignore[reportMissingImports]
    except ImportError as e:
        raise HTTPException(status_code=404, detail="flyball-linux is not installed here") from e
    return {"report": report(scan)}


class Query(BaseModel):
    text: str


@router.post("/links/{name}/query")
def query_link(rig: RigDep, name: str, body: Query) -> dict[str, str]:
    """Send `text` on a text link and return what came back; 409 for a link that is not one."""
    link = rig.links.get(name)
    if link is None:
        raise NotFoundError(f"Link {name!r} not found")
    query = getattr(link, "query", None)
    if not callable(query):
        raise ConflictError(f"Link {name!r} is not a text link: it has no query()")
    return {"reply": str(query(body.text))}


__all__ = ["router"]
