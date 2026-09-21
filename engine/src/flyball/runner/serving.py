"""Serving a built rig: the API, the MCP mount, retention, and the process handle."""

from __future__ import annotations

import logging
import os
import secrets
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from flyball.record.store import Store
from flyball.rig import Rig
from flyball.runtime.config import RigConfig, RunnerConfig
from flyball.runtime.retention import Retention

if TYPE_CHECKING:
    # A structural type, not an import: `flyball-sim` is an optional package
    # (see `flyball.interfaces.server.deps.Simulation`), so `runner`/`server` never
    # import the concrete `flyball_sim.simulation.Simulation` at module load.
    from flyball.interfaces.server.deps import Simulation

log = logging.getLogger("flyball.runner")


class Handle:
    """What the server may do to the process: read how it was started, stop it, restart it."""

    def __init__(self, settings: RunnerConfig, files: Sequence[Path], stop: Callable[[], None]):
        self.settings = settings
        self.files = list(files)
        self._stop = stop
        self.restarting = False

    def shutdown(self) -> None:
        """Stop serving; `serve` returns once the rig is stopped."""
        self._stop()

    def restart(self) -> None:
        """Stop serving, then start this process again with the same command line."""
        self.restarting = True
        self._stop()


def serve(
    rig: Rig,
    settings: RunnerConfig | None = None,
    *,
    simulation: Simulation | None = None,
    store: Store | None = None,
    config: RigConfig | None = None,
) -> None:
    """Serve `rig` until interrupted. The rig's devices must already be polling.

    Args:
        rig: The rig, built and with its devices polling.
        settings: How to serve -- the `runner:` section of a rig file with the
            command line's overrides applied ([RunnerConfig][flyball.runtime.config.RunnerConfig]);
            None: its defaults. Its `programs`, `tunings` and `drivers` are
            imported before serving (a missing directory is fine) and its
            `token`, `mcp`, `root_path`, `compose`, `allow_save` and
            `allow_shutdown` are what the API is let do; its `keep`, `keep_size`,
            `retain`, `rotate` and `max_store` are how the store is swept
            (`flyball.runtime.retention.Retention`).
        simulation: The knobs of a simulated rig, for `/api/sim`; None for hardware.
        store: Where sessions are kept, for `/api/history` and `/api/recording`,
            and where the scratch record goes while nothing is being recorded;
            None leaves those routes answering 503 and keeps no scratch.
        config: What the rig was built from, for `/api/rig/config`.

    A restart asked for over the API (`POST /api/runner/restart`) stops the
    rig and replaces this process with the same command line, once `serve`
    has unwound.
    """
    import uvicorn

    from flyball.interfaces.client import Rig as Client
    from flyball.interfaces.mcp.http import mount
    from flyball.interfaces.server import create_app, set_programmer, set_rig, set_simulation
    from flyball.interfaces.server.auth import signing_secret
    from flyball.interfaces.server.deps import (
        set_compose,
        set_drivers_dir,
        set_programs_dir,
        set_retention,
        set_rig_config,
        set_runner,
        set_store,
    )
    from flyball.interfaces.server.routes import dashboards
    from flyball.interfaces.server.routes.library import import_directory, load_tunings
    from flyball.model.catalog import ensure_discovered
    from flyball.sequencing import Programmer

    settings = settings or RunnerConfig()
    programs, tunings, drivers = settings.programs, settings.tunings, settings.drivers
    programmer = Programmer(rig)
    ensure_discovered()  # a caller that built `rig` without going through `main()` first
    set_rig(rig)
    set_programmer(programmer)
    set_simulation(simulation)
    set_store(store)
    set_programs_dir(programs)
    set_rig_config(config)
    set_compose(settings.compose)
    set_drivers_dir(drivers)
    if store is not None and programs is not None and programs.is_dir():
        imported = import_directory(store, programs, rig.clock.now_ns())
        log.info("programs from %s: %d imported", programs, len(imported))
    if tunings is not None:
        loaded = load_tunings(rig, tunings)
        log.info("tunings from %s: %d loaded", tunings, len(loaded))
    boards = None if programs is None else programs.parent / "dashboards"
    if store is not None and boards is not None and boards.is_dir():
        rows = dashboards.import_directory(store, boards, rig.name or "rig", rig.clock.now_ns())
        log.info("dashboards from %s: %d imported", boards, len(rows))
    auth = settings.auth
    # The MCP mount calls the runner back over loopback; on a password-only runner it
    # needs a token of its own, made here and never shown.
    internal = secrets.token_urlsafe(32) if auth.enabled and not auth.token else None
    app = create_app(
        auth,
        settings.root_path,
        secret=signing_secret(auth, settings.store),
        internal_token=internal,
    )
    if settings.mcp:  # `/mcp/<mode>`: a model's way in
        base = f"http://127.0.0.1:{settings.port}{settings.root_path or ''}"
        mount(app, Client(base, token=auth.token or internal))
    server = uvicorn.Server(
        uvicorn.Config(app, host=settings.host, port=settings.port, log_level=settings.log_level)
    )

    def stop() -> None:
        server.should_exit = True

    handle = Handle(settings, rig.files, stop)
    set_runner(handle)
    retention = None if store is None else Retention(rig, store, settings)
    set_retention(retention)
    if retention is not None:
        retention.start()
    try:
        server.run()
    finally:
        programmer.interrupt()
        if retention is not None:
            retention.stop()
        set_retention(None)
        set_runner(None)
        set_compose(False)
        set_rig_config(None)
        set_drivers_dir(None)
        set_programs_dir(None)
        set_store(None)
        set_simulation(None)
        set_programmer(None)
        set_rig(None)
        rig.stop()  # polling, writers, recording
    if handle.restarting:
        log.info("restarting: %s", " ".join(sys.argv))
        os.execv(sys.executable, [sys.executable, *sys.argv])
