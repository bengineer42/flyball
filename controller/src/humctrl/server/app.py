"""Application assembly: middleware, error translation, lifespan, routers."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from humctrl.error import (
    ConflictError,
    HardwareError,
    HumCtrlError,
    NotFoundError,
    NotReadyError,
    UnachievableError,
)
from humctrl.server.deps import current_manager
from humctrl.server.routes import (
    command_router,
    controller_router,
    pumps_router,
    rig_router,
    telemetry_router,
)

# The UI is served from its own dev server during development.
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Leave the rig as we found it.

    The server owns no topics of its own: telemetry subscribes to the manager's,
    so the two cannot disagree about what was published. On the way out the
    pumps are stopped and the loop halted, since nothing is left watching them.
    """
    try:
        yield
    finally:
        manager = current_manager()
        if manager is not None:
            with contextlib.suppress(Exception):
                manager.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="humctrl",
        summary="Split-range humidity control rig",
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
    # humctrl.error already says what a caller can do about each failure.
    # Starlette dispatches on the exception's MRO, so the HumCtrlError entry
    # catches anything not yet classified, and ValueError covers the domain's
    # out-of-range values (FlowError, HumidityRailError, PumpHumiditiesError).
    codes: dict[type[Exception], int] = {
        NotFoundError: 404,  # no such tuning or law
        ConflictError: 409,  # wrong state; stop or start something and retry
        UnachievableError: 422,  # well formed, but the numbers are unachievable
        ValueError: 422,  # anything else that is simply a bad value
        NotReadyError: 503,  # rig not configured, or no reading yet
        HardwareError: 503,  # a device failed; usually transient
        HumCtrlError: 409,  # fallback for anything unclassified
    }

    for error, code in codes.items():

        async def handler(request: Request, exc: Exception, code: int = code) -> JSONResponse:
            # ``code`` is bound as a default: without it every handler would
            # close over the loop variable and share the last value.
            return JSONResponse(status_code=code, content={"detail": str(exc)})

        app.add_exception_handler(error, handler)

    @app.get("/api/health", tags=["rig"])
    async def health() -> dict[str, Any]:
        return {"status": "ok", "rig_attached": current_manager() is not None}

    app.include_router(rig_router)
    app.include_router(controller_router)
    app.include_router(command_router)
    app.include_router(pumps_router)
    app.include_router(telemetry_router)
    return app


app = create_app()
