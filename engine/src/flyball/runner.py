"""Serve a rig described by a file.

    flyball-runner rig.toml
    flyball-runner rig.toml --host 0.0.0.0 --port 8000 --record
    flyball-runner furnace.yaml sim.yaml --set clock.speed=60

Builds the rig from the file (any of `.toml`, `.yaml`, `.json`), starts its
devices polling, optionally opens a recording session, and serves the HTTP
and websocket API until stopped. Several files layer, later overlaying earlier
(see [flyball.runtime.overlay][]); the store and the programs directory then
default off the first one. An application with hardware the rig file cannot
describe writes its own runner around [serve][flyball.runner.serve].
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from flyball.core.config import discover
from flyball.db.store import Store
from flyball.runtime.config import AuthConfig, RigConfig, RunnerConfig, resolve_documents
from flyball.runtime.drivers import load_drivers
from flyball.runtime.overlay import resolve_layers
from flyball.runtime.retention import Retention
from flyball.runtime.rig import Rig
from flyball.runtime.simulation import Simulation

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

    from flyball.client import Rig as Client
    from flyball.mcp.http import mount
    from flyball.programmer import Programmer
    from flyball.server import create_app, set_programmer, set_rig, set_simulation
    from flyball.server.auth import signing_secret
    from flyball.server.deps import (
        set_compose,
        set_drivers_dir,
        set_programs_dir,
        set_retention,
        set_rig_config,
        set_runner,
        set_store,
    )
    from flyball.server.routes import dashboards
    from flyball.server.routes.library import import_directory, load_tunings

    settings = settings or RunnerConfig()
    programs, tunings, drivers = settings.programs, settings.tunings, settings.drivers
    programmer = Programmer(rig)
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


def start(
    config: RigConfig, record: bool | None = None, store_path: str | Path = "flyball.sqlite"
) -> Rig:
    """Build the rig and, if asked or if the file says so, open a session on it."""
    rig, _ = start_with_store(config, record, store_path)
    return rig


def start_with_store(
    config: RigConfig, record: bool | None = None, store_path: str | Path = "flyball.sqlite"
) -> tuple[Rig, Store]:
    """`start`, also returning the store so the server can read history from it.

    The store is opened whether or not a session is: past sessions are
    readable and recording can be started from the API either way.
    """
    from flyball.db.sqlite import SqliteStore

    rig = config.build()
    store = SqliteStore(store_path)
    # A session still open in the store was left by a runner that died: close
    # it at its last sample, or it would look live and overlap the next one.
    for orphan in store.sessions():
        if orphan.open:
            store.end_session(orphan.id)
            log.warning("closed session %d, left open by an earlier run", orphan.id)
    keep_versions(
        rig, store, "resumed" if config.resumed else "loaded" if rig.files else "started bare"
    )
    if record if record is not None else config.recording:
        rig.start_recording(store, config=config.model_dump(mode="json"))
        log.info("recording to %s", store_path)
    return rig, store


START_REASONS = ("loaded", "started bare", "resumed")
"""Versions a start records, as against a change made through the API."""


def keep_versions(rig: Rig, store: Store, reason: str) -> None:
    """Record the rig as it stands, and every change to its composition from now on.

    A start whose rig is what the last version already says records nothing:
    a runner restarted on the same files does not fill the store.
    """

    def version(why: str) -> None:
        row = store.save_rig_version(
            rig.clock.now_ns(), why, rig.document(), [str(p) for p in rig.files]
        )
        log.info("rig version %d: %s", row.id, why)

    head = store.head_rig_version()
    if head is None or head.document != rig.document():
        version(reason)
    rig.on_change = version


def resumed(store_path: str | Path) -> RigConfig:
    """The store's head rig version as a config, files and all.

    Raises:
        ValueError: The store has no version to resume from.
    """
    from flyball.db.sqlite import SqliteStore

    store = SqliteStore(store_path)
    try:
        head = store.head_rig_version()
        if head is None:
            raise ValueError(f"{store_path}: no rig version to resume from")
        # The last change made through the API, not the last start: a plain
        # restart in between (an empty rig, the files as they were) is not
        # what `--resume` is asked for. Walk back from the head along parents.
        last = head
        while last.reason in START_REASONS and last.parent is not None:
            last = store.rig_version(last.parent)
    finally:
        store.close()
    config = RigConfig.model_validate(last.document)
    config.files = [Path(f) for f in last.files]
    config.resumed = True
    log.info("resuming rig version %d (%s)", last.id, last.reason)
    return config


def parser() -> argparse.ArgumentParser:
    """A flag mirroring a `runner:` key defaults to None -- unset -- so the file's value stands."""
    p = argparse.ArgumentParser(
        prog="flyball-runner", description="Serve a rig described by a file."
    )
    p.add_argument(
        "rig",
        type=Path,
        nargs="*",
        help="rig file(s): .toml, .yaml or .json; later overlay earlier. None: an empty rig,"
        " built up through the API (then --store says where sessions and versions go)",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="start from the store's last rig version instead of the files: what was added"
        " through the API and not saved comes back",
    )
    p.add_argument("--host", help="bind address (default: loopback only)")
    p.add_argument(
        "--compose",
        action="store_const",
        const=True,
        help="allow the rig to be built up over the API on a hardware rig"
        " (a simulated or bare rig always may)",
    )
    p.add_argument(
        "--password",
        default=os.environ.get("FLYBALL_PASSWORD") or None,
        help="password the UI's login page takes: a $scrypt$ line from `flyball password`, or"
        " plain text (env FLYBALL_PASSWORD); default: none",
    )
    p.add_argument(
        "--token",
        default=os.environ.get("FLYBALL_TOKEN") or None,
        help="bearer token for the CLI, MCP clients and scripts (env FLYBALL_TOKEN); default: none."
        " With neither this nor a password the runner is open",
    )
    p.add_argument(
        "--anonymous",
        choices=("none", "read"),
        default=os.environ.get("FLYBALL_ANONYMOUS") or None,
        help="what a caller with no session and no token may do: nothing, or read every GET and"
        " stream (env FLYBALL_ANONYMOUS); default: none",
    )
    p.add_argument(
        "--session",
        default=os.environ.get("FLYBALL_SESSION") or None,
        metavar="DURATION",
        help="how long a login lasts, e.g. 12h (env FLYBALL_SESSION; default 12h)",
    )
    p.add_argument(
        "--no-mcp",
        dest="mcp",
        action="store_const",
        const=False,
        default=False if os.environ.get("FLYBALL_NO_MCP") else None,
        help="do not mount the MCP servers at /mcp (env FLYBALL_NO_MCP=1); default: mounted",
    )
    p.add_argument(
        "--root-path",
        default=os.environ.get("FLYBALL_ROOT_PATH") or None,
        metavar="/PREFIX",
        help="serve everything under this path, e.g. /flyball/humidity (env FLYBALL_ROOT_PATH);"
        " default: the root",
    )
    p.add_argument(
        "--allow-save",
        action="store_const",
        const=True,
        help="let the API write rig files: /api/rig/save to a path, /api/sim/save; default: no",
    )
    p.add_argument(
        "--allow-shutdown",
        action="store_const",
        const=True,
        help="let the API stop or restart the runner (/api/runner/shutdown, /restart); default: no",
    )
    p.add_argument("--port", type=int, help="TCP port (default 8000)")
    p.add_argument(
        "--keep",
        default=os.environ.get("FLYBALL_KEEP") or None,
        metavar="DURATION",
        help="how much the scratch record holds while nothing is recorded, in the rig's clock"
        " (env FLYBALL_KEEP; default 1h; 0 keeps none)",
    )
    p.add_argument(
        "--keep-size",
        default=os.environ.get("FLYBALL_KEEP_SIZE") or None,
        metavar="SIZE",
        help="the most the scratch record may take on disk (env FLYBALL_KEEP_SIZE; default 256MB)",
    )
    p.add_argument(
        "--retain",
        default=os.environ.get("FLYBALL_RETAIN") or None,
        metavar="DURATION",
        help="delete an unpinned session this long after it ended, e.g. 30d"
        " (env FLYBALL_RETAIN; default 0: keep every session)",
    )
    p.add_argument(
        "--rotate",
        default=os.environ.get("FLYBALL_ROTATE") or None,
        metavar="DURATION",
        help="close a recording at this length and continue it in a new session, e.g. 24h"
        " (env FLYBALL_ROTATE; default 0: never)",
    )
    p.add_argument(
        "--max-store",
        default=os.environ.get("FLYBALL_MAX_STORE") or None,
        metavar="SIZE",
        help="keep the store under this size by deleting the oldest data of any kind, never"
        " pinned, e.g. 20GB (env FLYBALL_MAX_STORE; default 0: no cap)",
    )
    p.add_argument("--log-level", help="uvicorn's (default info)")
    p.add_argument("--record", action="store_true", help="open a recording session on start")
    p.add_argument(
        "--store",
        type=Path,
        help="where sessions are kept (default: '<rig>.sqlite' beside the first rig file)",
    )
    p.add_argument(
        "--programs",
        type=Path,
        help="directory of program files to import (default: 'programs' beside the first rig file)",
    )
    p.add_argument(
        "--tunings",
        type=Path,
        help="directory of control-law configs to load (default: 'tunings' beside the first rig"
        " file)",
    )
    p.add_argument(
        "--drivers",
        type=Path,
        help="directory of driver .py files to import at start and on /api/drivers/reload"
        " (default: 'drivers' beside the first rig file)",
    )
    p.add_argument(
        "--set",
        dest="sets",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="override a value after loading, e.g. devices.furnace.config.noise=0.3; repeatable",
    )
    return p


def settle(
    section: RunnerConfig | None, args: argparse.Namespace, first: Path, name: str | None = None
) -> RunnerConfig:
    """The file's `runner:` section under the command line, with every directory resolved.

    A flag given (or its environment variable) beats the file; a path in the
    file is taken relative to the first rig file's directory; a directory
    not named at all is the conventional one beside that file. The store is
    `--store`, else `store` in the file, else `<store_dir>/<name>.sqlite`
    (the rig's name, else the file's stem), else `<rig>.sqlite` beside the file.
    """
    given = {
        key: value
        for key in RunnerConfig.model_fields
        if key != "auth" and (value := getattr(args, key, None)) is not None
    }
    settings = (section or RunnerConfig()).model_copy(update=given)
    auth = {
        key: value
        for key in AuthConfig.model_fields
        if (value := getattr(args, key, None)) is not None
    }
    if auth:
        settings.auth = settings.auth.model_copy(update=auth)
    for key in ("store", "store_dir", "programs", "tunings", "drivers"):
        path: Path | None = getattr(settings, key)
        if path is not None and key not in given and not path.is_absolute():
            setattr(settings, key, first.parent / path)  # the file's: relative to the rig
    if settings.store is None:
        if settings.store_dir is not None:
            settings.store = settings.store_dir / f"{name or first.stem}.sqlite"
        else:
            settings.store = first.with_suffix(".sqlite")
    for key in ("programs", "tunings", "drivers"):
        if getattr(settings, key) is None:
            setattr(settings, key, first.parent / key)
    return settings


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=(args.log_level or "info").upper())
    first = args.rig[0] if args.rig else Path("rig")
    try:
        discover()
        document: dict[str, Any] = {"name": first.stem}  # bare: built through the API
        files: list[Path] = []
        if args.rig and not args.resume:
            document, files = resolve_documents(args.rig, args.sets)
        # The runner section first: it may say where the drivers are, and
        # the rig file may name a driver from there.
        section = RunnerConfig.model_validate(document.get("runner") or {})
        name = document.get("name")
        settings = settle(section, args, first, name if isinstance(name, str) else None)
        logging.getLogger().setLevel(settings.log_level.upper())
        assert settings.store is not None and settings.drivers is not None
        report = load_drivers(settings.drivers)
        for stem, error in report.errors.items():
            log.warning("drivers/%s.py: %s", stem, error)
        if args.resume:
            config = resumed(settings.store)
        else:
            config = RigConfig.model_validate(document)
            config.files = files
    except Exception as e:  # a bad file is the user's problem, not a traceback
        names = ", ".join(str(p) for p in args.rig) or "(no rig file)"
        print(f"flyball-runner: {names}: {e}", file=sys.stderr)
        return 2
    settings.store.parent.mkdir(parents=True, exist_ok=True)  # a store_dir that is not there yet
    rig, store = start_with_store(
        config, record=True if args.record else None, store_path=settings.store
    )
    simulation = None
    if config.simulated and args.rig:
        # What `sim save` writes back: the layers merged, but before the board
        # was applied, so a board's links are not inlined into the rig file.
        layered, _ = resolve_layers([Path(p) for p in args.rig], args.sets)
        simulation = Simulation(rig, config, layered, first)
        log.info("a simulation: %s plants, clock at %gx", len(simulation.plants), simulation.speed)
    log.info("serving %s on %s:%d", config.name or first.name, settings.host, settings.port)
    serve(rig, settings, simulation=simulation, store=store, config=config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
