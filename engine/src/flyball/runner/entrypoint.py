"""`flyball-runner`'s entry point: parse the command line, build the rig, serve it.

Not named `main.py`: `__init__.py` re-exports its `main` function under that
same name, which would shadow this submodule on `flyball.runner.main` --
`pytest.monkeypatch.setattr("flyball.runner.main.serve", ...)` would then
resolve to the function, not this module, and silently patch nothing. Patch
`flyball.runner.entrypoint.serve` instead.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import IO, Any

from flyball.foundation.optional import require
from flyball.model.catalog import Catalogs, set_catalog
from flyball.rig.stopping import Stopper
from flyball.runtime.config import RigConfig, RunnerConfig, resolve_documents
from flyball.runtime.drivers import load_drivers
from flyball.runtime.overlay import resolve_layers

from . import frontdir, locking, logs
from .cli import parser, settle
from .serving import serve
from .starting import BuildFailed, resumed, start_with_store
from .stopping import install_break_glass

log = logging.getLogger("flyball.runner")

BAD_CONFIG = 2
"""Exit code: the rig file or its config is wrong; starting again will not help."""
RIG_BUSY = 3
"""Exit code: another runner holds this rig's lock."""
FRONT_DIR = 4
"""Exit code: `--front-dir` is unsafe or incomplete; the front writes it again."""


def _refuse(args: Any, e: Exception) -> int:
    names = ", ".join(str(p) for p in args.rig) or "(no rig file)"
    print(f"flyball-runner: {names}: {e}", file=sys.stderr)
    return BAD_CONFIG


def _needed_extras(args: Any) -> list[str]:
    """Which top-level modules this run needs, from the command line alone.

    fastapi/uvicorn serve at all; mcp/httpx are only needed when the MCP
    servers are mounted (the default -- `--no-mcp` turns it off); yaml only
    when a rig file actually is one.
    """
    needed = ["fastapi", "uvicorn"]
    if args.mcp is not False:
        needed += ["mcp", "httpx"]
    if any(Path(p).suffix.lower() in (".yaml", ".yml") for p in args.rig):
        needed.append("yaml")
    return needed


def _attached_stopper() -> Stopper | None:
    """The stopper of the rig serving attached, or None while there is none yet.

    Read from the server's module only if it is loaded (nothing attaches a rig before
    then), so a SIGUSR1 during startup imports nothing on the handler's thread.
    """
    deps = sys.modules.get("flyball.interfaces.server.deps")
    current = getattr(deps, "current_stopper", None)
    return None if current is None else current()


def main(argv: Sequence[str] | None = None) -> int:
    # SIGUSR1 first of all: `flyball stop` sends it to the pid the lock files name, which
    # they do long before the rig is up, and its default action would end the runner.
    install_break_glass(_attached_stopper)
    args = parser().parse_args(argv)
    logs.configure(args.log_level or "info")
    if args.front_dir is None:
        return _main(args)
    # Before the rig's lock, the drivers, the store: before anything. `runner.lock` before
    # the key: a front never rewrites the key of a front-dir whose lock is held, so from the
    # moment this runner has read it, a front that starts meanwhile finds it (and adopts it)
    # instead of giving the key it holds to a second runner.
    try:
        frontdir.check(args.front_dir)
        mine = locking.hold_front(args.front_dir)
    except frontdir.Unusable as e:
        print(f"flyball-runner: --front-dir {args.front_dir}: {e}", file=sys.stderr)
        return FRONT_DIR
    except locking.RigBusy as e:
        print(f"flyball-runner: {e}", file=sys.stderr)
        return RIG_BUSY
    except OSError as e:  # runner.lock a symlink (O_NOFOLLOW), not ours to open, ...
        print(f"flyball-runner: --front-dir {args.front_dir}: runner.lock: {e}", file=sys.stderr)
        return FRONT_DIR
    with mine:
        try:
            front = frontdir.read(args.front_dir)
        except frontdir.Unusable as e:
            print(f"flyball-runner: --front-dir {args.front_dir}: {e}", file=sys.stderr)
            return FRONT_DIR
        return _main(args, front, mine)


def _main(args: Any, front: frontdir.FrontDir | None = None, mine: IO[str] | None = None) -> int:
    """Everything after the front-dir: the rig file, the rig's lock, the rig."""
    first = args.rig[0] if args.rig else Path("rig")
    # Checked before any of the real work below, so a bare `pip install flyball`
    # names the extra to add instead of failing opaquely, deep inside `serve()`
    # or a lazy YAML/`flyball_sim` import.
    require("flyball-runner", "server", _needed_extras(args))
    try:
        catalog = Catalogs()
        catalog.discover()
        set_catalog(catalog)
        document: dict[str, Any] = {"name": first.stem}  # bare: built through the API
        files: list[Path] = []
        if args.rig and not args.resume:
            document, files = resolve_documents(args.rig, args.sets)
        # The runner section first: it may say where the drivers are, and
        # the rig file may name a driver from there.
        section = RunnerConfig.model_validate(document.get("runner") or {})
        name = document.get("name")
        settings = settle(section, args, first, name if isinstance(name, str) else None, files)
        logging.getLogger().setLevel(settings.log_level.upper())
        assert settings.store is not None and settings.drivers is not None
    except Exception as e:  # a bad file is the user's problem, not a traceback
        return _refuse(args, e)
    try:  # before any driver is imported, link opened or the store touched
        lock = locking.hold(settings.store)
    except locking.RigBusy as e:
        print(f"flyball-runner: {e}", file=sys.stderr)
        return RIG_BUSY
    with lock:
        if mine is not None:
            locking.name_front(mine, name if isinstance(name, str) else first.stem)
        return _run(args, settings, document, files, first, front)


def _run(
    args: Any,
    settings: RunnerConfig,
    document: dict[str, Any],
    files: list[Path],
    first: Path,
    front: frontdir.FrontDir | None = None,
) -> int:
    """Build the rig and serve it, the rig's lock held."""
    assert settings.store is not None and settings.drivers is not None
    try:
        report = load_drivers(settings.drivers)
        for stem, error in report.errors.items():
            log.warning("drivers/%s.py: %s", stem, error)
        if args.resume:
            config = resumed(settings.store)
        else:
            config = RigConfig.model_validate(document)
            config.files = files
    except Exception as e:  # a bad file is the user's problem, not a traceback
        return _refuse(args, e)
    settings.store.parent.mkdir(parents=True, exist_ok=True)  # a store_dir that is not there yet
    try:
        rig, store = start_with_store(
            config, record=True if args.record else None, store_path=settings.store
        )
    except BuildFailed as e:  # a driver refused its config, a device is not there
        return _refuse(args, e)
    simulation = None
    if config.simulated and args.rig:
        try:
            from flyball_sim.simulation import Simulation
        except ImportError:
            names = ", ".join(str(p) for p in args.rig)
            print(
                f"flyball-runner: {names}: every link is simulated, but flyball-sim is not"
                " installed here",
                file=sys.stderr,
            )
            return BAD_CONFIG
        # What `sim save` writes back: the layers merged, but before the board
        # was applied, so a board's links are not inlined into the rig file.
        layered, _ = resolve_layers([Path(p) for p in args.rig], args.sets)
        simulation = Simulation(rig, config, layered, first)
        log.info("a simulation: %s plants, clock at %gx", len(simulation.plants), simulation.speed)
    where = front.endpoint if front is not None else f"{settings.host}:{settings.port}"
    log.info("serving %s on %s", config.name or first.name, where)
    # uvicorn's own graceful shutdown (its "Shutting down" / "Application shutdown
    # complete" logging) already runs by the time this is caught -- the interrupt
    # still escapes uvicorn's internals and would otherwise print a raw traceback
    # here on top of that, for no reason: the process is exiting cleanly either way.
    with contextlib.suppress(KeyboardInterrupt):
        serve(
            rig,
            settings,
            simulation=simulation,
            store=store,
            config=config,
            insecure_open=bool(args.insecure_open),
            front=front,
        )
    return 0
