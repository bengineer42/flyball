"""Building a rig from a config and opening a store on it, with version bookkeeping."""

from __future__ import annotations

import logging
from pathlib import Path

from flyball.record.store import Store
from flyball.rig import Rig
from flyball.runtime.config import RigConfig

log = logging.getLogger("flyball.runner")


class BuildFailed(Exception):
    """The rig could not be built from its config: a driver refused it, a device was not there.

    The config's to fix, not the process's: `flyball-runner` says so in one line and
    exits 2, as for a file that does not validate, rather than crash into a restart loop.
    """


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

    Raises:
        BuildFailed: The rig could not be built; nothing is left running.
    """
    try:
        rig = config.build()
    except Exception as e:
        raise BuildFailed(str(e) or type(e).__name__) from e
    try:
        store = _open(config, rig, record, store_path)
    except BaseException:
        rig.stop()  # nothing left polling a rig that will not be served
        raise
    return rig, store


def _open(config: RigConfig, rig: Rig, record: bool | None, store_path: str | Path) -> Store:
    from flyball.record.sqlite import SqliteStore

    store = SqliteStore(store_path)
    # A delete cut off part-way (it goes in batches) is finished before anything reads.
    for half in store.deleting_sessions():
        store.delete_session(half.id)
        log.warning("finished deleting session %d, begun by an earlier run", half.id)
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
    return store


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
    from flyball.record.sqlite import SqliteStore

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
