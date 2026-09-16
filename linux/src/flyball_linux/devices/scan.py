"""Which of a device's own signals to read now, for a driver that reads several over one bus.

A device is polled on the smallest `poll_s` in its tree; a signal with a
longer one of its own is due only every so often. Every multi-signal
driver here asks a [Scan][flyball_linux.devices.scan.Scan] which of its own
signals (registers, channels -- never `conditions` or `last.*`) are due at
an instant, so the rule lives once.
"""

from __future__ import annotations

from collections.abc import Iterable

from flyball.core.signal import Access, Signal


class Scan:
    """Which of a driver's own signals are due, each on its own `poll_s`."""

    def __init__(self) -> None:
        self._last: dict[Signal, int] = {}

    def _is_due(self, signal: Signal, time_ns: int) -> bool:
        # A tenth of the period short still counts: a 2 s signal on a device
        # polled every second is read on the second poll, not the third, when
        # a scaled clock's threads arrive a little early.
        if (period := signal.poll_s) is None or (last := self._last.get(signal)) is None:
            return True
        return time_ns - last >= 0.9 * period * 1e9

    def due(self, signals: Iterable[Signal], time_ns: int, *, whole: bool) -> list[Signal]:
        """Which of `signals` -- the driver's own map -- to read at `time_ns`, and mark them read.

        `whole` -- a read someone asked for -- takes every readable one;
        the runtime's poll takes the publishing ones whose period has
        passed. The caller passes its own registers or channels, never the
        device's whole tree, so `conditions` and `last.*` are never among
        them.
        """
        found: list[Signal] = []
        for signal in signals:
            if whole:
                if Access.R not in signal.access:
                    continue
            elif Access.P not in signal.access or not self._is_due(signal, time_ns):
                continue
            found.append(signal)
            self._last[signal] = time_ns
        return found


__all__ = ["Scan"]
