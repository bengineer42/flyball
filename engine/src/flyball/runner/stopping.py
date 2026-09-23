"""Stopping the rig from the runner's side: the break-glass signal.

The types are [flyball.rig.stopping][]'s, re-exported here. `SIGUSR1` is meant to run
the same [Stopper][flyball.rig.stopping.Stopper] as `POST /api/rig/stop`, without exiting,
so a stop works with no front and no credential; the OS's own signal permission is the
check.
"""

from __future__ import annotations

from collections.abc import Callable

from flyball.rig.stopping import Actor, DeviceStop, Stopper, StopReport

__all__ = ["Actor", "DeviceStop", "StopReport", "Stopper", "install_break_glass"]


def install_break_glass(stopper: Callable[[], Stopper | None]) -> None:
    """Install the `SIGUSR1` handler that stops the rig without exiting. Not yet: a no-op.

    Once wired, `SIGUSR1` runs `stopper().stop(Actor(sub="local:signal", ..., via="signal"),
    "SIGUSR1")` and logs the report; main thread only; it never exits the process.
    """
