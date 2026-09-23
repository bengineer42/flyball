"""Serving a built rig: the API, the MCP mount, retention, and the process handle."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from flyball.record.store import Store
from flyball.rig import Rig
from flyball.runtime.config import Exposure, RigConfig, RunnerConfig, settle_exposure
from flyball.runtime.retention import Retention

from . import logs

if TYPE_CHECKING:
    # A structural type, not an import: `flyball-sim` is an optional package
    # (see `flyball.interfaces.server.deps.Simulation`), so `runner`/`server` never
    # import the concrete `flyball_sim.simulation.Simulation` at module load.
    from flyball.interfaces.server.deps import Simulation

    from .frontdir import FrontDir

log = logging.getLogger("flyball.runner")


class Handle:
    """What the server may do to the process: read how it was started, stop it, restart it."""

    def __init__(
        self,
        settings: RunnerConfig,
        files: Sequence[Path],
        stop: Callable[[], None],
        exposure: Exposure | None = None,
    ):
        self.settings = settings
        self.files = list(files)
        self._stop = stop
        self.restarting = False
        self.exposure = exposure

    def shutdown(self) -> None:
        """Stop serving; `serve` returns once the rig is stopped."""
        self._stop()

    def restart(self) -> None:
        """Stop serving, then start this process again with the same command line."""
        self.restarting = True
        self._stop()


def _interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def _terminate_as_interrupt() -> signal.Handlers | Callable[..., object] | int | None:
    """Make SIGTERM stop the runner the way Ctrl-C does; the handler it replaced, if any.

    uvicorn shuts down gracefully on either signal, then restores the handler it found
    and raises the signal again. For SIGINT that is Python's KeyboardInterrupt, so
    `serve`'s cleanup runs; for SIGTERM it was the default -- the process died by the
    signal and the rig was never stopped (the session left open, the programmer not
    interrupted). flyballd and systemd both stop with SIGTERM. Main thread only.
    """
    if threading.current_thread() is not threading.main_thread():
        return None
    return signal.signal(signal.SIGTERM, _interrupt)


def _print_link(nonce: str, host: str, settings: RunnerConfig) -> None:
    """The one-time sign-in link, on stderr: open it once in the browser, within ten minutes."""
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    shown = f"[{host}]" if ":" in host else host
    root = (settings.root_path or "").rstrip("/")
    url = f"http://{shown}:{settings.port}{root}/api/auth/link?n={nonce}"
    print(
        f"flyball-runner: sign in to the UI once, within 10 minutes: {url}",
        file=sys.stderr,
        flush=True,
    )


def serve(
    rig: Rig,
    settings: RunnerConfig | None = None,
    *,
    simulation: Simulation | None = None,
    store: Store | None = None,
    config: RigConfig | None = None,
    insecure_open: bool = False,
    front: FrontDir | None = None,
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
        insecure_open: Serve an open runner (no token) on the address
            asked for even beyond loopback (`--insecure-open`). Without it such a
            runner is served on 127.0.0.1, the same port, with one line on stderr
            saying why; `/api/auth` and `/api/health` report it either way
            ([settle_exposure][flyball.runtime.config.settle_exposure]).
        front: The front-dir a front started this runner with (`--front-dir`): bind its
            endpoint only, take only the principal it signs, serve no UI. `runner.auth`,
            `host` and `port` are then ignored, with one line on stderr if they said
            anything. None: a bare runner, whose token (if any) gets a one-time sign-in
            link printed at start.

    A restart asked for over the API (`POST /api/runner/restart`) stops the
    rig and replaces this process with the same command line, once `serve`
    has unwound.
    """
    import uvicorn

    from flyball.interfaces.client import Rig as Client
    from flyball.interfaces.mcp.http import mount
    from flyball.interfaces.server import create_app, set_programmer, set_rig, set_simulation
    from flyball.interfaces.server.auth import Fronted
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
    exposure = settle_exposure(
        settings, insecure_open, endpoint=None if front is None else front.endpoint
    )
    if exposure.warning and (exposure.open or exposure.notes):  # loudly: exposure changed
        print(f"flyball-runner: WARNING: {exposure.warning}", file=sys.stderr, flush=True)
    elif exposure.warning:
        log.warning("%s", exposure.warning)
    if exposure.restricted:
        settings = settings.model_copy(update={"host": exposure.host})
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
    app = create_app(
        auth,
        settings.root_path,
        front=None if front is None else Fronted(front.key, front.aud),
        port=settings.port,
        open_network=exposure.open_network,
    )
    if front is not None and front.network == "unix":
        bind: dict[str, Any] = {"uds": front.address}
    elif front is not None:
        bind = {"host": front.host, "port": front.port}
    else:
        bind = {"host": settings.host, "port": settings.port}
    if settings.mcp:  # `/mcp/<mode>`: a model's way in
        # The tools call the runner back over HTTP. Bare: loopback TCP, with the token. A
        # fronted runner's inner calls need a principal minted per call over its endpoint:
        # the MCP re-mint (package A6) supplies that; until then they are refused.
        base = f"http://127.0.0.1:{settings.port}{settings.root_path or ''}"
        mount(app, Client(base, token=None if front is not None else auth.token), name=rig.name)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            **bind,
            log_level=settings.log_level,
            proxy_headers=False,  # the peer is the peer: no X-Forwarded-For past the limiter
        )
    )
    if front is None and auth.enabled:
        _print_link(app.state.door.mint_link(), exposure.host, settings)
    logs.stamp_uvicorn()  # its handlers exist once the Config is made

    def stop() -> None:
        server.should_exit = True

    handle = Handle(settings, rig.files, stop, exposure)
    set_runner(handle)
    retention = None if store is None else Retention(rig, store, settings)
    set_retention(retention)
    if retention is not None:
        retention.start()
    from flyball.interfaces.server.deps import current_stopper
    from flyball.runner.stopping import install_break_glass

    install_break_glass(current_stopper)  # SIGUSR1: stop the rig, without exiting
    previous = _terminate_as_interrupt()
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
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    if handle.restarting:
        # The interpreter's own argv, so `python -m flyball.runner` restarts as `-m` too (and
        # `--front-dir` comes back with the rest: fronted stays fronted).
        argv = [sys.executable, *sys.orig_argv[1:]]
        log.info("restarting: %s", " ".join(argv))
        os.execv(sys.executable, argv)
