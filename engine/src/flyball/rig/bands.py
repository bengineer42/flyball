"""A signal's `warning` and `alarm` bands, held as conditions on the signal as readings arrive.

The rig raises and clears an alarm; a widget's limits are only how it draws.
Each reading on a banded signal is checked on the delivery that brings it:

- outside `alarm`: the signal holds `band_alarm` (`error`);
- else outside `warning`: it holds `band_warning` (`warning`);
- a signal holds at most one of the two.

A worse band is raised at once, on the first reading beyond it (and a held
`band_warning` cleared). A better one waits: the held condition clears, or
`band_alarm` gives way to `band_warning`, only once readings have been
below it for `max(2·poll_s, 1 s)` on the rig clock without a break -- a
signal without `poll_s` (a push) waits 1 s. A value hovering at the edge
therefore raises once and stays raised, not once per crossing.

Only a finite number is judged. A reading that is not one (None, NaN, a
string) leaves everything as it was: what a band does on no value is a
separate rule.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from flyball.foundation.device import Code, Conditions, Reading, Severity, Signal
from flyball.foundation.device.signal import Bounds

HOLD_MIN_S = 1.0
"""The least a band condition waits, with readings back inside, before it clears."""

_SEVERITY = {Code.BAND_WARNING: Severity.WARNING, Code.BAND_ALARM: Severity.ERROR}
_RANK = {None: 0, Code.BAND_WARNING: 1, Code.BAND_ALARM: 2}


def hold_s(signal: Signal) -> float:
    """How long readings must stay back inside before `signal`'s band condition clears."""
    poll_s = signal.poll_s
    return HOLD_MIN_S if poll_s is None else max(2 * poll_s, HOLD_MIN_S)


def _outside(value: float, bounds: Bounds | None) -> str | None:
    """`"low"` or `"high"` when `value` is outside `bounds`; None inside, or with no band."""
    if bounds is None:
        return None
    if value < bounds[0]:
        return "low"
    if value > bounds[1]:
        return "high"
    return None


class Bands:
    """The band conditions of every signal, kept from its readings on the delivery thread.

    What each signal holds is the store's; this keeps only, per signal, when
    its readings last went below the band it holds, for the hysteresis.
    """

    def __init__(self, conditions: Conditions, now_ns: Callable[[], int]) -> None:
        self.conditions = conditions
        self.now_ns = now_ns
        self._below_since: dict[Signal, int] = {}

    def check(self, reading: Reading) -> None:
        """Raise, keep or clear `reading.signal`'s band condition for this value."""
        signal = reading.signal
        spec = signal.spec
        if spec.warning is None and spec.alarm is None:
            return
        value = reading.value
        if isinstance(value, bool) or not isinstance(value, int | float):
            return
        if not math.isfinite(value):
            return
        code: Code | None = None
        bounds: Bounds | None = None
        side = _outside(value, spec.alarm)
        if side is not None:
            code, bounds = Code.BAND_ALARM, spec.alarm
        elif (side := _outside(value, spec.warning)) is not None:
            code, bounds = Code.BAND_WARNING, spec.warning
        held = self._held(signal)
        if _RANK[code] >= _RANK[held]:
            self._below_since.pop(signal, None)
            if code is not None and code != held:
                self._raise(signal, code, side, value, bounds)
                if held is not None:
                    self.conditions.clear(signal, held, message=f"{code} raised")
            return
        # Below what it holds: better, but only once it has stayed better long enough.
        now = self.now_ns()
        since = self._below_since.setdefault(signal, now)
        if now - since < hold_s(signal) * 1e9:
            return
        del self._below_since[signal]
        assert held is not None
        if code is not None:
            self._raise(signal, code, side, value, bounds)
        self.conditions.clear(
            signal, held, message=f"back inside {'alarm' if held == Code.BAND_ALARM else 'warning'}"
        )

    def forget(self, signal: Signal) -> None:
        """Drop what is kept for `signal`: its device is gone (the store clears its conditions)."""
        self._below_since.pop(signal, None)

    def _held(self, signal: Signal) -> Code | None:
        if self.conditions.get(signal, Code.BAND_ALARM) is not None:
            return Code.BAND_ALARM
        if self.conditions.get(signal, Code.BAND_WARNING) is not None:
            return Code.BAND_WARNING
        return None

    def _raise(
        self, signal: Signal, code: Code, side: str | None, value: float, bounds: Bounds | None
    ) -> None:
        assert side is not None and bounds is not None
        band = "alarm" if code == Code.BAND_ALARM else "warning"
        unit = f" {signal.unit.symbol}" if signal.unit.symbol else ""
        where = "below" if side == "low" else "above"
        details: dict[str, Any] = {"side": side, "value": value, "bounds": list(bounds)}
        message = f"{value:g}{unit} {where} the {band} band [{bounds[0]:g}, {bounds[1]:g}]"
        self.conditions.set(signal, code, _SEVERITY[code], message, details)


__all__ = ["HOLD_MIN_S", "Bands", "hold_s"]
