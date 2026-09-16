"""Which of a device's signals to read now, for a driver that reads several over one bus.

A device is polled on the smallest `poll_s` in its tree; a signal with a
longer one of its own is due only every so often. Every multi-signal
driver here asks a [Scan][flyball_linux.devices.scan.Scan] which signals
under a node are due at an instant, so the rule lives once.
"""

from __future__ import annotations

from flyball.core.signal import Access, Node, Signal


class Scan:
    """Which publishing signals under a node are due, each on its own `poll_s`."""

    def __init__(self) -> None:
        self._last: dict[Signal, int] = {}

    def _is_due(self, signal: Signal, time_ns: int) -> bool:
        # A tenth of the period short still counts: a 2 s signal on a device
        # polled every second is read on the second poll, not the third, when
        # a scaled clock's threads arrive a little early.
        if (period := signal.poll_s) is None or (last := self._last.get(signal)) is None:
            return True
        return time_ns - last >= 0.9 * period * 1e9

    def due(self, node: Node, time_ns: int, *, whole: bool) -> list[Signal]:
        """The signals under `node` to read at `time_ns`, and mark them read.

        `whole` -- a read someone asked for -- takes every readable signal;
        the runtime's poll takes the publishing ones whose period has
        passed.
        """
        found: list[Signal] = []
        for signal in node.walk():
            if whole:
                if Access.R not in signal.access:
                    continue
            elif Access.P not in signal.access or not self._is_due(signal, time_ns):
                continue
            found.append(signal)
            self._last[signal] = time_ns
        return found


__all__ = ["Scan"]
