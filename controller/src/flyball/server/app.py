"""Application assembly: middleware, error translation, lifespan, routers."""

from __future__ import annotations

import contextlib
import hmac
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from flyball.core.errors import (
    ConflictError,
    FlyballError,
    HardwareError,
    NotFoundError,
    NotReadyError,
    UnachievableError,
)
from flyball.server.deps import current_retention, current_rig
from flyball.server.routes import (
    composition_router,
    controllers_router,
    daemon_router,
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
    schema_router,
    sim_router,
    telemetry_router,
    waits_router,
)

# The UI is served from its own dev server during development.
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """On exit stop polling and close the session, so nothing runs on a server that has gone."""
    try:
        yield
    finally:
        rig = current_rig()
        if rig is not None:
            with contextlib.suppress(Exception):
                rig.polling.stop_all()
            # Close the session so it does not stay "open" forever in the store;
            # the daemon's sweeps stop first, or they would open the scratch record again.
            if (retention := current_retention()) is not None:
                with contextlib.suppress(Exception):
                    retention.stop()
            with contextlib.suppress(Exception):
                rig.stop_recording()


class BearerToken:
    """Refuse every request and websocket without the token.

    `Authorization: Bearer`, or `?token=` where a browser cannot set a
    header: a websocket, and a plain navigation (an export link), which is
    a GET. One shared secret for everything the daemon serves -- `/api`,
    `/ws`, `/mcp` -- since any of them can drive the rig. Constant-time
    compare; 401 with a `detail` like every other refusal.
    """

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token

    def _given(self, scope: Any) -> str | None:
        headers: dict[bytes, bytes] = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode(errors="replace")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        if scope["type"] == "websocket" or scope.get("method") == "GET":
            tokens: list[str] = parse_qs(scope.get("query_string", b"").decode()).get("token", [])
            return tokens[0] if tokens else None
        return None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        given = self._given(scope)
        if given is not None and hmac.compare_digest(given, self.token):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401, "reason": "token required"})
            return
        response = JSONResponse(
            status_code=401,
            content={"detail": "This daemon needs a bearer token (Authorization: Bearer ...)"},
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)


class RootPath:
    """Serve everything under one path prefix: `/flyball/humidity/api/...`.

    For a daemon behind a proxy that does not rewrite: a request under the
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
            status_code=404, content={"detail": f"This daemon serves under {self.prefix}"}
        )
        await response(scope, receive, send)


def create_app(token: str | None = None, root_path: str | None = None) -> FastAPI:
    """The app.

    With `token`, everything it serves needs it (see `BearerToken`); with
    `root_path`, everything it serves is under that prefix (see `RootPath`).
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
    app.include_router(daemon_router)
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
    if token:
        app.add_middleware(BearerToken, token=token)
    if root_path and root_path != "/":
        if not root_path.startswith("/"):
            raise ValueError(f"root_path must start with '/': {root_path!r}")
        app.add_middleware(RootPath, prefix=root_path.rstrip("/"))
    return app


app = create_app()
