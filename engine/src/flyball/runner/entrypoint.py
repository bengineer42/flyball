"""`flyball-runner`'s entry point: parse the command line, build the rig, serve it.

Not named `main.py`: `__init__.py` re-exports its `main` function under that
same name, which would shadow this submodule on `flyball.runner.main` --
`pytest.monkeypatch.setattr("flyball.runner.main.serve", ...)` would then
resolve to the function, not this module, and silently patch nothing. Patch
`flyball.runner.entrypoint.serve` instead.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import IO, Any

from flyball.foundation.device import Code, Severity
from flyball.foundation.optional import require
from flyball.model.catalog import Catalogs, set_catalog
from flyball.rig.stopping import Stopper
from flyball.runtime.config import RigConfig, RunnerConfig, resolve_documents, saved_overlay_path
from flyball.runtime.drivers import load_drivers
from flyball.runtime.edits import Origin, newer_files, overrides, roll_back
from flyball.runtime.overlay import resolve_layers

from . import frontdir, locking, logs
from .cli import parser, settle
from .serving import EDIT_ENV, EDIT_FAILED_ENV, ServeFailed, serve
from .starting import BuildFailed, resumed, start_with_store
from .stopping import install_break_glass

log = logging.getLogger("flyball.runner")

BAD_CONFIG = 2
"""Exit code: the rig file or its config is wrong; starting again will not help."""
RIG_BUSY = 3
"""Exit code: another runner holds this rig's lock."""
FRONT_DIR = 4
"""Exit code: `--front-dir` is unsafe or incomplete; the front writes it again."""
SERVE_FAILED = 5
"""Exit code: the runner could not serve -- its socket or port could not be bound, or its
server failed to start. Not busy (that is 3, a lock held): starting again may work.

The whole table, with `flyball`'s own codes: book/src/7-reference/cli.md#exit-codes."""


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
    origin = Origin(tuple(args.rig), tuple(args.sets), bool(args.resume))
    edit = _taken(EDIT_ENV)  # this start is a rig edit's: roll it back if it does not build
    failed = _taken(EDIT_FAILED_ENV)  # this start is a rollback's: say so on the rig
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
        if edit is not None:
            _roll_back(edit, e, origin, settings.store)
        return _refuse(args, e)
    settings.store.parent.mkdir(parents=True, exist_ok=True)  # a store_dir that is not there yet
    try:
        record = True if args.record or (edit or "").endswith(":1") else None
        at = int(edit.split(":")[0]) if edit else None
        rig, store = start_with_store(config, record=record, store_path=settings.store, edit=at)
    except BuildFailed as e:  # a driver refused its config, a device is not there
        if edit is not None:
            _roll_back(edit, e, origin, settings.store)
        return _refuse(args, e)
    if failed is not None:
        _not_built(rig, failed)
    if not origin.stored:
        _say_overlay(origin, config.files)
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
    try:
        with contextlib.suppress(KeyboardInterrupt):
            serve(
                rig,
                settings,
                simulation=simulation,
                store=store,
                config=config,
                insecure_open=bool(args.insecure_open),
                front=front,
                origin=origin,
            )
    except ServeFailed as e:
        names = ", ".join(str(p) for p in args.rig) or "(no rig file)"
        print(f"flyball-runner: {names}: could not serve on {where}: {e}", file=sys.stderr)
        return SERVE_FAILED
    return 0


# region After a rig edit (D-051)


def _taken(name: str) -> str | None:
    """The environment variable's value, removed: a later plain restart does not inherit it."""
    return os.environ.pop(name, None)


def _roll_back(edit: str, error: Exception, origin: Origin, store_path: Path) -> None:
    """The edited rig did not build: put the one before back, and start again once, on it.

    The overlay the edit replaced comes back (`.prev`), a `restored from M` version is
    written with version M's document (the head, for `--resume`), and the process execs
    with `FLYBALL_EDIT_FAILED` set, which the next start raises as a condition. With no
    version to go back to, nothing is done and the start fails as any bad rig does.
    """
    from flyball.record.sqlite import SqliteStore

    version, before, _ = [*edit.split(":"), "", ""][:3]
    if not before:
        log.error("the edit to rig version %s did not build, and there is none before it", version)
        return
    previous = int(before)
    log.error(
        "the edit to rig version %s did not build (%s); going back to version %d",
        version,
        error,
        previous,
    )
    roll_back(origin)
    store = SqliteStore(store_path)
    try:
        row = store.rig_version(previous)
        store.save_rig_version(time.time_ns(), f"restored from {previous}", row.document, row.files)
    finally:
        store.close()
    os.environ[EDIT_FAILED_ENV] = json.dumps({
        "version": int(version),
        "previous": previous,
        "error": str(error),
    })
    argv = [sys.executable, *sys.orig_argv[1:]]
    os.execv(sys.executable, argv)


def _not_built(rig: Any, failed: str) -> None:
    """The `edit_not_built` condition on the rig: which edit, why, and what runs instead."""
    try:
        details = json.loads(failed)
        message = (
            f"edit to version {details['version']} did not build: {details['error']};"
            f" running version {details['previous']}"
        )
    except (ValueError, KeyError, TypeError):
        details, message = None, "a rig edit did not build; running the version before it"
    log.error("%s", message)
    rig.conditions.set(rig, Code.EDIT_NOT_BUILT, Severity.ERROR, message, details)


def _say_overlay(origin: Origin, files: list[Path]) -> None:
    """Log which keys the saved overlay sets, and warn of a rig file edited after it."""
    path = saved_overlay_path(origin.layers[0])
    if not any(f.resolve() == path.resolve() for f in files):
        return
    keys = overrides(path)
    if keys:
        log.info("saved overlay %s sets %s", path, ", ".join(keys))
    for stale in newer_files(path, files):
        log.warning(
            "%s was changed after the saved overlay %s, which sets the same keys and wins:"
            " the overlay's values are in force",
            stale,
            path,
        )


# endregion
