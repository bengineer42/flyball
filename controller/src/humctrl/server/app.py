"""Application assembly: middleware, error translation, lifespan, routers."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from humctrl.manager import HumCtrlError
from humctrl.server.deps import current_manager
from humctrl.server.routes import pumps_router, rig_router, telemetry_router

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

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        # The domain raises ValueError (FlowError and friends) for out-of-range
        # or unreachable flow. That is a bad request, not a server fault.
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(HumCtrlError)
    async def humctrl_error_handler(request: Request, exc: HumCtrlError) -> JSONResponse:
        # Missing pumps, recorder, controller or target: the rig is not in a
        # state where this request makes sense.
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/api/health", tags=["rig"])
    async def health() -> dict[str, Any]:
        return {"status": "ok", "rig_attached": current_manager() is not None}

    app.include_router(rig_router)
    app.include_router(pumps_router)
    app.include_router(telemetry_router)
    return app


app = create_app()
