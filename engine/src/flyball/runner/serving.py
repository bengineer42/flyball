"""Serving a built rig: the API, the MCP mount, retention, and the process handle."""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
import signal
import socket
import stat
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from flyball.record.store import Store
from flyball.rig import Rig
from flyball.runtime.config import Exposure, RigConfig, RunnerConfig, settle_exposure
from flyball.runtime.retention import Retention

from . import logs
from .locking import redacted

if TYPE_CHECKING:
    from fastapi import FastAPI

    from flyball.interfaces.client import Rig as Client

    # A structural type, not an import: `flyball-sim` is an optional package
    # (see `flyball.interfaces.server.deps.Simulation`), so `runner`/`server` never
    # import the concrete `flyball_sim.simulation.Simulation` at module load.
    from flyball.interfaces.server.deps import Simulation
    from flyball.interfaces.server.principal import Claims

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


GRACEFUL_SHUTDOWN_S = 5
"""How long uvicorn waits for open connections at shutdown before cancelling them."""


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


def _ignore_hangup() -> None:
    """Make SIGHUP a no-op (D-038): a dropped terminal must not kill the runner.

    `flyball run` puts the runner in its own process group and the front ignores SIGHUP
    too, but a bare `flyball-runner` run directly in a terminal gets the same protection
    here -- there is no other place its signal handling is set up. Logged once so a
    hangup is visible in the log without stopping anything. Main thread only, and a
    no-op on a platform with no SIGHUP.
    """
    hup = getattr(signal, "SIGHUP", None)
    if hup is None or threading.current_thread() is not threading.main_thread():
        return

    def handler(signum: int, frame: object) -> None:
        log.info("terminal hung up; the rig keeps running")

    signal.signal(hup, handler)


class ServeFailed(Exception):
    """The runner could not serve: its socket or port was not bound, or its server not started.

    Not a busy rig (no lock is held by another), so another try may work.
    """


def _owner_socket(path: str) -> tuple[socket.socket, str, int]:
    """A unix socket bound at `path`, mode 0600, not yet listening; `path` and its inode.

    Only this uid (and root) can connect, whatever the directory's mode; nobody can connect
    before the chmod, since nothing connects to a socket that does not listen yet. A stale
    socket at `path` (a runner before this one) is replaced, as uvicorn would.
    """
    with contextlib.suppress(FileNotFoundError):
        if stat.S_ISSOCK(os.lstat(path).st_mode):
            os.unlink(path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(path)
        os.chmod(path, 0o600)
        return sock, path, os.lstat(path).st_ino
    except OSError:
        sock.close()
        raise


def _remove_socket(sock: socket.socket, path: str, inode: int) -> None:
    """Close `sock` and unlink `path` if it is still the socket bound there (not a successor's)."""
    sock.close()
    with contextlib.suppress(OSError):
        if os.lstat(path).st_ino == inode:
            os.unlink(path)


# region The MCP mount: its tools call the runner back, as whoever called them


def mcp_client(settings: RunnerConfig, front: FrontDir | None) -> Client:
    """The client the MCP tools call the runner back with.

    Over the front-dir's endpoint when fronted (its unix socket, or its loopback port);
    over loopback TCP when bare.
    """
    from flyball.interfaces.client import Rig as Client

    root = settings.root_path or ""
    if front is not None and front.network == "unix":
        return Client(f"http://localhost{root}", token="", uds=front.address)
    if front is not None:
        return Client(f"http://{front.address}{root}", token="")
    return Client(f"http://127.0.0.1:{settings.port}{root}", token="")


def mcp_caps() -> dict[str, frozenset[str]]:
    """Per MCP mode, the verbs a tool's call may carry: the mode's own and every lower mode's.

    A mode serves its tier's tools and every tier below (`mcp.tools.MODES`), so its cap is
    the union of `verbs.MCP_MODES` over those modes -- `MCP_MODES` alone says who may
    enter, and with the placeholder vocabulary `operate` would lose `read`.
    """
    from flyball.interfaces.mcp.tools import MODES
    from flyball.interfaces.server.verbs import MCP_MODES

    return {
        mode: frozenset().union(*(MCP_MODES[m] for m, t in MODES.items() if t <= tier))
        for mode, tier in MODES.items()
    }


def mcp_signer(key: bytes, aud: str) -> Callable[[Claims | None, str], str]:
    """`sign(caller, mode)` for [mount][flyball.interfaces.mcp.http.mount].

    Each call mints a principal now, for one request, with `key` for `aud` (the door's own).
    For a caller: its `sub`, `sid`, `kind`, `nm`, `cip` and `sch`, `scp` = its verbs ∩ the
    mode's cap (`mcp_caps`), `via: "mcp"`. For None, the runner's own read: `runner:mcp`,
    a service with `read` only. No principal is kept: each lives 60 s and is used once.
    """
    from flyball.interfaces.server.principal import LIFETIME, Claims, mint
    from flyball.interfaces.server.verbs import READ

    caps = mcp_caps()
    sid = f"mcp-{secrets.token_urlsafe(12)}"

    def sign(caller: Claims | None, mode: str) -> str:
        now = int(time.time())
        if caller is None:
            claims = Claims(
                sub="runner:mcp", sid=sid, scp=frozenset({READ}), kind="service",
                aud=aud, cip="", sch="http", iat=now, exp=now + LIFETIME,
            )  # fmt: skip
        else:
            scp = caller.scp & caps[mode]
            claims = replace(caller, scp=scp, aud=aud, via="mcp", iat=now, exp=now + LIFETIME)
        return mint(key, claims)

    return sign


def mount_mcp(
    app: FastAPI, name: str | None, settings: RunnerConfig, front: FrontDir | None
) -> None:
    """`/mcp/<mode>` on `app`, its tools calling back as their caller (`mcp_signer`)."""
    from flyball.interfaces.mcp.http import mount

    door = app.state.door
    mount(app, mcp_client(settings, front), name=name, sign=mcp_signer(door.key, door.aud))


# endregion


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
    bound = None
    if front is not None and front.network == "unix":
        # uvicorn would make the socket itself, 0666; bound here, it is 0600 before it listens.
        try:
            bound = _owner_socket(front.address)
        except OSError as e:
            raise ServeFailed(f"binding {front.address}: {e}") from None
        bind: dict[str, Any] = {"fd": bound[0].fileno()}
    elif front is not None:
        bind = {"host": front.host, "port": front.port}
    else:
        bind = {"host": settings.host, "port": settings.port}
    if settings.mcp:  # `/mcp/<mode>`: a model's way in
        mount_mcp(app, rig.name, settings, front)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            **bind,
            log_level=settings.log_level,
            proxy_headers=False,  # the peer is the peer: no X-Forwarded-For past the limiter
            # A websocket or a download left open would hold the shutdown -- and the
            # recording's close -- for ever; after this uvicorn cancels them (D-045).
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
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
    _ignore_hangup()  # SIGHUP: log it, keep running (D-038)
    from uvicorn.config import STARTUP_FAILURE

    try:
        server.run()
    except SystemExit as e:
        # uvicorn's exit when it cannot start (a port taken, its app's startup failed) is 3,
        # which is flyball's "rig busy": said as what it is instead.
        if e.code != STARTUP_FAILURE:
            raise
        raise ServeFailed("the server could not start; its error is logged above") from None
    finally:
        if bound is not None:
            _remove_socket(*bound)
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
        rig.close()  # polling, writers, recording, links
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    if handle.restarting:
        # The interpreter's own argv, so `python -m flyball.runner` restarts as `-m` too (and
        # `--front-dir` comes back with the rest: fronted stays fronted).
        argv = [sys.executable, *sys.orig_argv[1:]]
        log.info("restarting: %s", " ".join(redacted(argv)))
        os.execv(sys.executable, argv)
