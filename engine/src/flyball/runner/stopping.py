"""Stopping the rig from the runner's side: the break-glass signal.

The types are [flyball.rig.stopping][]'s, re-exported here. `SIGUSR1` runs the same
[Stopper][flyball.rig.stopping.Stopper] as `POST /api/rig/stop`, without exiting, so a
stop works with no front and no credential; the OS's own signal permission (the same
user, or root) is the check. `flyball stop` sends it to the pid in `runner.lock` or
`<store>.lock` when the front cannot be reached. Each break-glass stop is a row in the
action audit ([record_stop][flyball.interfaces.server.audit.record_stop]).
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
from collections.abc import Callable
from types import FrameType

from flyball.interfaces.server.audit import record_stop
from flyball.rig.stopping import Actor, DeviceStop, InterimStopper, Stopper, StopReport

__all__ = ["Actor", "DeviceStop", "InterimStopper", "StopReport", "Stopper", "install_break_glass"]

log = logging.getLogger("flyball.stop")

SIGNAL_ACTOR = Actor(
    sub="local:signal",
    sid="",
    kind="human",
    via="signal",
    detail="SIGUSR1 from a process of the runner's user or root; the sender is not recorded",
)
"""Who a break-glass stop is recorded as.

The sender's pid and uid are not known: Python's handlers get no `siginfo`, and
`sigwaitinfo` sees a signal only if every thread blocks it, which the rig's threads,
started before serving, do not.
"""


def install_break_glass(stopper: Callable[[], Stopper | None]) -> None:
    """Make `SIGUSR1` stop the rig without exiting: `stopper().stop(SIGNAL_ACTOR, "SIGUSR1")`.

    The stop runs on a thread of its own, not in the handler: the handler runs on the
    main thread, which is serving, and a stop may wait on a program's worker. Its report
    is logged as one `stop report: {json}` line at WARNING; with nothing to stop
    (`stopper()` is None) that is said instead. Main thread only (elsewhere, and where
    there is no `SIGUSR1`, a no-op); the handler stays installed for the process's life.
    """
    usr1 = getattr(signal, "SIGUSR1", None)
    if usr1 is None or threading.current_thread() is not threading.main_thread():
        return

    def handler(signum: int, frame: FrameType | None) -> None:
        threading.Thread(target=_stop, args=(stopper,), name="break-glass", daemon=True).start()

    signal.signal(usr1, handler)


def _stop(stopper: Callable[[], Stopper | None]) -> None:
    target = stopper()
    if target is None:
        log.warning("SIGUSR1: no rig attached; nothing to stop")
        return
    at_ns = time.time_ns()
    try:
        report = target.stop(SIGNAL_ACTOR, "SIGUSR1")
    except Exception:
        log.exception("SIGUSR1: the stop failed")
        record_stop(SIGNAL_ACTOR, "SIGUSR1", at_ns, done=False)
        return
    record_stop(SIGNAL_ACTOR, "SIGUSR1", report.at_ns)
    log.warning("stop report: %s", json.dumps(report.as_dict()))
