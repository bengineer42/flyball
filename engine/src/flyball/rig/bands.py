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

Only a finite number is judged. A reading at a limit whose true value may
lie beyond a band edge on that side (`at_limit: high` below a `high` edge
it could be past) is unknown to that band: it leaves everything as it was.

A reading with no value leaves the held band condition as it was too; the
band is unknown. When the no-value is a fault (`invalid`; `stale` by age),
the signal's `on_no_value` decides: `fire` (the default with an `alarm`
band) raises `band_unknown` once the fault has lasted its grace,
`max(2·poll_s, 1 s)` for `invalid` and none for `stale` (its
`stale_after_s` was the grace); `ignore` (the default with only a
`warning` band) raises nothing. A benign no-value (`not_applicable`) never
fires, nor does `stale` because the device is offline or its writes fail:
the device's own condition already counts. The episode ends, and
`band_unknown` clears, after 3 readings in a row with a value.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from flyball.foundation.device import (
    Code,
    Conditions,
    NoValue,
    OnNoValue,
    Quality,
    Reading,
    Reason,
    Severity,
    Signal,
)
from flyball.foundation.device.signal import Bounds

HOLD_MIN_S = 1.0
"""The least a band condition waits, with readings back inside, before it clears."""

EPISODE_ENDS_AFTER = 3
"""Readings with a value, in a row, that end a no-value episode."""

_DEVICE_COUNTED = frozenset({Reason.DEVICE_OFFLINE, Reason.DEVICE_HUNG, Reason.WRITE_FAILED})
"""Stale reasons a device condition already counts: never `band_unknown` as well."""

_SEVERITY = {Code.BAND_WARNING: Severity.WARNING, Code.BAND_ALARM: Severity.ERROR}
_RANK = {None: 0, Code.BAND_WARNING: 1, Code.BAND_ALARM: 2}


def hold_s(signal: Signal) -> float:
    """How long readings must stay back inside before `signal`'s band condition clears."""
    poll_s = signal.poll_s
    return HOLD_MIN_S if poll_s is None else max(2 * poll_s, HOLD_MIN_S)


def on_no_value(signal: Signal) -> OnNoValue:
    """The signal's `on_no_value`, or its default: `fire` with an `alarm` band, else `ignore`."""
    spec = signal.spec
    if spec.on_no_value is not None:
        return spec.on_no_value
    return OnNoValue.FIRE if spec.alarm is not None else OnNoValue.IGNORE


def _beyond(value: float, side: str, bounds: Bounds | None) -> bool:
    """Whether a railed `value` could be past `bounds`' edge on `side`: that band is unknown."""
    if bounds is None:
        return False
    return value <= bounds[0] if side == "low" else value >= bounds[1]


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
        self._fault_since: dict[Signal, int] = {}
        """When each signal's no-value episode began: its first fault-class no-value."""
        self._fresh: dict[Signal, int] = {}
        """Readings with a value in a row since, for a signal in an episode."""

    def check(self, reading: Reading) -> None:
        """Raise, keep or clear `reading.signal`'s band conditions for this value."""
        signal = reading.signal
        spec = signal.spec
        if spec.warning is None and spec.alarm is None:
            return
        value = reading.value
        if isinstance(value, NoValue):
            self._no_value(signal, value)
            return
        if signal in self._fault_since:
            self._value_again(signal)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return
        if not math.isfinite(value):
            return
        if (side := reading.at_limit) is not None and (
            _beyond(value, side, spec.alarm) or _beyond(value, side, spec.warning)
        ):
            return  # railed: the true value may be past the edge, so the band is unknown
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

    def _no_value(self, signal: Signal, value: NoValue) -> None:
        """A reading with no value: the band is unknown; `band_unknown` if it is a fault's."""
        self._below_since.pop(signal, None)  # a break: back inside has to start again
        self._fresh.pop(signal, None)
        if not value.quality.fault or value.reason in _DEVICE_COUNTED:
            return
        if on_no_value(signal) is OnNoValue.IGNORE:
            return
        now = self.now_ns()
        since = self._fault_since.setdefault(signal, now)
        if self.conditions.get(signal, Code.BAND_UNKNOWN) is not None:
            return
        grace_s = 0.0 if value.quality is Quality.STALE else hold_s(signal)
        if now - since < grace_s * 1e9:
            return
        why = f": {value.reason}" if value.reason else ""
        self.conditions.set(
            signal,
            Code.BAND_UNKNOWN,
            Severity.ERROR if signal.spec.alarm is not None else Severity.WARNING,
            f"no value ({value.quality.value}{why}) for {(now - since) / 1e9:.1f} s:"
            " its band is unknown",
            {"quality": value.quality.value, "reason": value.reason, "side": value.side},
        )

    def _value_again(self, signal: Signal) -> None:
        """A reading with a value inside an episode: the third in a row ends it."""
        fresh = self._fresh[signal] = self._fresh.get(signal, 0) + 1
        if fresh < EPISODE_ENDS_AFTER:
            return
        del self._fault_since[signal]
        del self._fresh[signal]
        self.conditions.clear(
            signal, Code.BAND_UNKNOWN, message=f"{fresh} readings with a value: known again"
        )

    def forget(self, signal: Signal) -> None:
        """Drop what is kept for `signal`: its device is gone (the store clears its conditions)."""
        self._below_since.pop(signal, None)
        self._fault_since.pop(signal, None)
        self._fresh.pop(signal, None)

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


__all__ = ["EPISODE_ENDS_AFTER", "HOLD_MIN_S", "Bands", "hold_s", "on_no_value"]
