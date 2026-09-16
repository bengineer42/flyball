"""Serve a rig described by a file.

    flyball-daemon rig.toml
    flyball-daemon rig.toml --host 0.0.0.0 --port 8000 --record
    flyball-daemon furnace.yaml sim.yaml --set clock.speed=60

Builds the rig from the file (any of `.toml`, `.yaml`, `.json`), starts its
readers, optionally opens a recording session, and serves the HTTP and
websocket API until stopped. Several files layer, later overlaying earlier
(see [flyball.runtime.overlay][]); the store and the programs directory then
default off the first one. An application with hardware the rig file cannot
describe writes its own daemon around [serve][flyball.daemon.serve].
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from flyball.core.config import discover
from flyball.db.store import Store
from flyball.runtime.config import RigConfig, resolve_documents
from flyball.runtime.overlay import resolve_layers
from flyball.runtime.rig import Rig
from flyball.runtime.simulation import Simulation

log = logging.getLogger("flyball.daemon")


def serve(
    rig: Rig,
    host: str = "127.0.0.1",
    port: int = 8000,
    log_level: str = "info",
    simulation: Simulation | None = None,
    store: Store | None = None,
    programs: Path | None = None,
) -> None:
    """Serve `rig` until interrupted. The rig's readers must already be running.

    Args:
        rig: The rig, built and with its readers polling.
        host: Bind address; loopback unless the rig should be reachable.
        port: TCP port.
        log_level: uvicorn's.
        simulation: The knobs of a simulated rig, for `/api/sim`; None for hardware.
        store: Where sessions are kept, for `/api/history` and `/api/recording`;
            None leaves those routes answering 503.
        programs: A directory of program files, imported into the library on
            start and whenever the library is asked to rescan.
    """
    import uvicorn

    from flyball.programmer import Programmer
    from flyball.server import create_app, set_programmer, set_rig, set_simulation
    from flyball.server.deps import set_programs_dir, set_store
    from flyball.server.routes import dashboards
    from flyball.server.routes.library import import_directory

    programmer = Programmer(rig)
    set_rig(rig)
    set_programmer(programmer)
    set_simulation(simulation)
    set_store(store)
    set_programs_dir(programs)
    if store is not None and programs is not None and programs.is_dir():
        imported = import_directory(store, programs, rig.clock.now_ns())
        log.info("programs from %s: %d imported", programs, len(imported))
    boards = None if programs is None else programs.parent / "dashboards"
    if store is not None and boards is not None and boards.is_dir():
        rows = dashboards.import_directory(store, boards, rig.name, rig.clock.now_ns())
        log.info("dashboards from %s: %d imported", boards, len(rows))
    try:
        uvicorn.run(create_app(), host=host, port=port, log_level=log_level)
    finally:
        programmer.interrupt()
        set_programs_dir(None)
        set_store(None)
        set_simulation(None)
        set_programmer(None)
        set_rig(None)
        rig.stop()  # polling, writers, recording


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
    # A session still open in the store was left by a daemon that died: close
    # it at its last sample, or it would look live and overlap the next one.
    for orphan in store.sessions():
        if orphan.open:
            store.end_session(orphan.id)
            log.warning("closed session %d, left open by an earlier run", orphan.id)
    if record if record is not None else config.recording:
        rig.start_recording(store, config=config.model_dump(mode="json"))
        log.info("recording to %s", store_path)
    return rig, store


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flyball-daemon", description="Serve a rig described by a file."
    )
    p.add_argument(
        "rig",
        type=Path,
        nargs="+",
        help="rig file(s): .toml, .yaml or .json; later overlay earlier",
    )
    p.add_argument("--host", default="127.0.0.1", help="bind address (default: loopback only)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--log-level", default="info")
    p.add_argument("--record", action="store_true", help="open a recording session on start")
    p.add_argument(
        "--store",
        type=Path,
        default=None,
        help="where sessions are kept (default: '<rig>.sqlite' beside the first rig file)",
    )
    p.add_argument(
        "--programs",
        type=Path,
        default=None,
        help="directory of program files to import (default: 'programs' beside the first rig file)",
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


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=args.log_level.upper())
    first = args.rig[0]
    try:
        discover()
        document, _ = resolve_documents(args.rig, args.sets)
        config = RigConfig.model_validate(document)
    except Exception as e:  # a bad file is the user's problem, not a traceback
        names = ", ".join(str(p) for p in args.rig)
        print(f"flyball-daemon: {names}: {e}", file=sys.stderr)
        return 2
    rig, store = start_with_store(
        config,
        record=True if args.record else None,
        store_path=args.store if args.store is not None else first.with_suffix(".sqlite"),
    )
    simulation = None
    if config.simulated:
        # What `sim save` writes back: the layers merged, but before the board
        # was applied, so a board's links are not inlined into the rig file.
        layered, _ = resolve_layers([Path(p) for p in args.rig], args.sets)
        simulation = Simulation(rig, config, layered, first)
        log.info("a simulation: %s plants, clock at %gx", len(simulation.plants), simulation.speed)
    log.info("serving %s on %s:%d", config.name or first.name, args.host, args.port)
    programs = args.programs if args.programs is not None else first.parent / "programs"
    serve(rig, args.host, args.port, args.log_level, simulation, store, programs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
