"""One runner per rig: an exclusive lock beside the store, taken before anything is built.

A runner a front started also holds `runner.lock` in its front-dir (`hold_front`).
"""

from __future__ import annotations

import fcntl
import os
import sys
import time
from pathlib import Path
from typing import IO

FRONT_PATIENCE_S = 2.0
"""How long `hold_front` keeps trying for `runner.lock` before it gives up (`RigBusy`).

A front asks "does a runner live here?" with a shared lock held for an instant, and holds the
lock while it writes the directory; a runner taking the lock at that instant waits it out.
"""


class RigBusy(Exception):
    """Another runner holds the rig's lock."""


def lock_path(store: Path) -> Path:
    """`<store>.lock`: the store names the rig's sessions, versions and scratch record."""
    return store.with_name(store.name + ".lock")


def hold(store: Path) -> IO[str]:
    """Take the rig's lock for the life of the process, or raise `RigBusy` naming the holder.

    `flock`, so the lock goes with the process however it ends (a crash, SIGKILL,
    `execv` on a restart -- the descriptor is not inherited) and a stale file is no
    obstacle. Taken before any link is opened, the store touched or the rig built: a
    second runner for the same rig must not close the live one's session or reach its
    hardware. The file holds the holder's pid and command line, for the message.
    """
    path = lock_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")  # ruff: ignore[open-file-with-context-handler] -- the caller holds it
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.seek(0)
        holder = handle.read().strip() or "unknown"
        handle.close()
        raise RigBusy(
            f"this rig is already being run by another runner ({holder}): it holds {path}."
            " Stop that one first, or give this one its own --store"
        ) from None
    os.chmod(path, 0o600)
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid {os.getpid()}: {' '.join(sys.argv)}\n")
    handle.flush()
    return handle


def hold_front(front_dir: Path, rig: str = "") -> IO[str]:
    """Take `<front-dir>/runner.lock` for the life of the process: "a runner of mine lives here".

    The front never rewrites the key of a front-dir whose lock is held, and reads it to tell
    a live runner from a dead one -- so it is taken first, before the runner reads the
    front-dir's `key` (and before the rig's own lock, `hold`): a front that starts meanwhile
    then finds the runner, instead of rewriting the key under it. Checked with
    [frontdir.check][flyball.runner.frontdir.check] first. A front's probe or write holds the
    lock for an instant; that is waited out for `FRONT_PATIENCE_S`, then `RigBusy`: another
    runner holds it. The file holds `pid <n>`, then `pid <n> rig <name>` once `name_front`
    names the rig.
    """
    path = front_dir / "runner.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    handle = os.fdopen(fd, "r+", encoding="utf-8")
    deadline = time.monotonic() + FRONT_PATIENCE_S
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() < deadline:
                time.sleep(0.02)
                continue
            holder = handle.read().strip() or "unknown"
            handle.close()
            raise RigBusy(f"another runner lives in {front_dir} ({holder})") from None
    os.chmod(path, 0o600)
    name_front(handle, rig)
    return handle


def name_front(handle: IO[str], rig: str) -> None:
    """Write `pid <n> rig <name>` into a held `runner.lock` (`pid <n>` with no name)."""
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid {os.getpid()} rig {rig}\n" if rig else f"pid {os.getpid()}\n")
    handle.flush()
