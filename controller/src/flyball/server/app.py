"""Application assembly: middleware, error translation, lifespan, routers."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

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
from flyball.server.deps import current_rig
from flyball.server.routes import (
    actuators_router,
    dashboards_router,
    events_router,
    export_router,
    history_router,
    library_router,
    loops_router,
    program_router,
    readers_router,
    recording_router,
    rig_router,
    schema_router,
    signals_router,
    sim_router,
    telemetry_router,
)

# The UI is served from its own dev server during development.
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Attach telemetry to the rig's topics; on exit stop the pumps and halt the loop."""
    try:
        yield
    finally:
        rig = current_rig()
        if rig is not None:
            with contextlib.suppress(Exception):
                rig.readers.stop_all()
            # Close the session so it does not stay "open" forever in the store.
            with contextlib.suppress(Exception):
                rig.stop_recording()


def create_app() -> FastAPI:
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

    app.include_router(loops_router)  # before rig_router: its /loops/schema must beat /loops/{name}
    app.include_router(rig_router)
    app.include_router(actuators_router)
    app.include_router(readers_router)
    app.include_router(recording_router)
    app.include_router(signals_router)
    app.include_router(events_router)
    app.include_router(program_router)
    app.include_router(schema_router)
    app.include_router(sim_router)
    app.include_router(history_router)
    app.include_router(export_router)
    app.include_router(dashboards_router)
    app.include_router(library_router)
    app.include_router(telemetry_router)
    return app


app = create_app()
