"""Application assembly: middleware, error translation, lifespan, routers."""

from __future__ import annotations

import contextlib
import secrets
from collections.abc import AsyncIterator
from importlib import resources
from pathlib import Path
from typing import Any

from anyio import to_thread
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from flyball.foundation.errors import (
    ConflictError,
    FlyballError,
    HardwareError,
    NotFoundError,
    NotReadyError,
    UnachievableError,
)
from flyball.interfaces.server.auth import Auth
from flyball.interfaces.server.deps import current_retention, current_rig
from flyball.interfaces.server.routes import (
    composition_router,
    controllers_router,
    dashboards_router,
    devices_router,
    drivers_router,
    events_router,
    export_router,
    history_router,
    library_router,
    program_router,
    read_router,
    recording_router,
    rig_router,
    runner_router,
    schema_router,
    sim_router,
    telemetry_router,
    waits_router,
)
from flyball.interfaces.server.routes.auth import router as auth_router
from flyball.runtime.config import AuthConfig

# The UI is served from its own dev server during development.
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def _find_dashboard_dist() -> Path:
    """Locate the built dashboard's static files, installed wheel or dev checkout alike.

    An installed wheel force-includes `ui/apps/dashboard/dist` at
    `flyball/server/static` (see `pyproject.toml`), so the package-relative path
    resolves correctly wherever the package actually lives. In this monorepo's own
    dev checkout, though, nothing puts files there unless someone has run `hatch
    build`/`uv build` locally -- `importlib.resources.files("flyball")` just
    resolves to `engine/src/flyball`, and `server/static` won't exist under it. So
    a dev checkout falls back to the old monorepo-relative path (`ui/apps/dashboard/dist`
    next to `engine/`), which keeps `make test`/local runs showing the UI without
    requiring a wheel build first. Neither path existing (headless/no-UI install,
    or a dev checkout that's never run `npm run build`) is fine -- the caller checks
    `is_dir()` before mounting.
    """
    installed = resources.files("flyball") / "server" / "static"
    if isinstance(installed, Path) and installed.is_dir():
        return installed
    # this file -> server -> interfaces -> flyball -> src -> engine -> repo root
    return Path(__file__).resolve().parents[5] / "ui" / "apps" / "dashboard" / "dist"


DASHBOARD_DIST = _find_dashboard_dist()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """On exit stop polling and close the session, so nothing runs on a server that has gone."""
    try:
        yield
    finally:
        # Joins threads and writes the store: on a worker thread, as every store call is.
        await to_thread.run_sync(_stop)


def _stop() -> None:
    rig = current_rig()
    if rig is not None:
        with contextlib.suppress(Exception):
            rig.polling.stop_all()
        # Close the session so it does not stay "open" forever in the store;
        # the runner's sweeps stop first, or they would open the scratch record again.
        if (retention := current_retention()) is not None:
            with contextlib.suppress(Exception):
                retention.stop()
        with contextlib.suppress(Exception):
            rig.stop_recording()


class _Installed:
    """Wraps an already built middleware so `add_middleware` can install it."""

    def __init__(self, app: Any, instance: Any) -> None:
        instance.app = app
        self.instance = instance

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        await self.instance(scope, receive, send)


class RootPath:
    """Serve everything under one path prefix: `/flyball/humidity/api/...`.

    For a runner behind a proxy that does not rewrite: a request under the
    prefix is routed as if it were at the root (Starlette strips
    `root_path`, and builds `/docs` links with it); anything else is 404.
    Lifespan passes through, so the app's exit hooks still run.
    """

    def __init__(self, app: Any, prefix: str) -> None:
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        if path == self.prefix or path.startswith(self.prefix + "/"):
            scope["root_path"] = self.prefix
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4404, "reason": "not found"})
            return
        response = JSONResponse(
            status_code=404, content={"detail": f"This runner serves under {self.prefix}"}
        )
        await response(scope, receive, send)


def create_app(
    auth: AuthConfig | None = None,
    root_path: str | None = None,
    *,
    secret: bytes | None = None,
    internal_token: str | None = None,
    login_delay: float = 0.5,
) -> FastAPI:
    """The app.

    With `auth` naming a password or a token, the API is behind the door (see
    [flyball.interfaces.server.auth][]; `secret` signs the sessions, `internal_token`
    is the runner's own way in for its MCP mount); without, the runner is open, to
    loopback names only. With `root_path`, everything it serves is under that prefix
    (see `RootPath`).
    """
    app = FastAPI(
        title="flyball",
        summary="flyball control rig",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # One handler per error base rather than per class: the hierarchy in
    # flyball.error already says what a caller can do about each failure.
    # Starlette dispatches on the exception's MRO, so the FlyballError entry
    # catches anything not yet classified, and ValueError covers the domain's
    # out-of-range values (FlowError, HumidityRailError, PumpHumiditiesError).
    codes: dict[type[Exception], int] = {
        NotFoundError: 404,  # no such tuning or law
        ConflictError: 409,  # wrong state; stop or start something and retry
        UnachievableError: 422,  # well formed, but the numbers are unachievable
        ValueError: 422,  # anything else that is simply a bad value
        NotReadyError: 503,  # rig not configured, or no reading yet
        HardwareError: 503,  # a device failed; usually transient
        FlyballError: 409,  # fallback for anything unclassified
    }

    for error, code in codes.items():

        def handler(request: Request, exc: Exception, code: int = code) -> JSONResponse:
            # `code` is bound as a default: without it every handler would
            # close over the loop variable and share the last value.
            return JSONResponse(status_code=code, content={"detail": str(exc)})

        app.add_exception_handler(error, handler)

    app.include_router(controllers_router)
    app.include_router(runner_router)
    app.include_router(rig_router)
    app.include_router(devices_router)
    app.include_router(composition_router)
    app.include_router(drivers_router)
    app.include_router(read_router)
    app.include_router(recording_router)
    app.include_router(waits_router)
    app.include_router(events_router)
    app.include_router(program_router)
    app.include_router(schema_router)
    app.include_router(sim_router)
    app.include_router(history_router)
    app.include_router(export_router)
    app.include_router(dashboards_router)
    app.include_router(library_router)
    app.include_router(telemetry_router)
    app.include_router(auth_router)
    # Every runner has the door: an open one (no password, no token) still refuses other
    # names for itself and other sites' pages; `app.state.auth` is None there, which the
    # routes read as "open".
    door = Auth(
        app,
        auth if auth is not None else AuthConfig(),
        secret if secret is not None else secrets.token_bytes(32),
        internal_token=internal_token,
        delay=login_delay,
    )
    app.state.auth = None if door.open else door
    # `add_middleware` would build its own instance; the routes need this one.
    app.add_middleware(_Installed, instance=door)
    if root_path and root_path != "/":
        if not root_path.startswith("/"):
            raise ValueError(f"root_path must start with '/': {root_path!r}")
        app.add_middleware(RootPath, prefix=root_path.rstrip("/"))
    # Registered last so it doesn't shadow the API routers above; a headless/no-UI
    # install (no built dist) just keeps today's API-only behaviour.
    if DASHBOARD_DIST.is_dir():
        app.mount("/", StaticFiles(directory=DASHBOARD_DIST, html=True), name="dashboard")
    return app


app = create_app()
