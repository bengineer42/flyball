"""Live values: what a `driver: values` signal's value came from, and keeping it across restarts.

Each write of a values signal is logged (`value_written`) and kept in the
store's `live_value` table -- the value, who wrote it and when, the unit, and
the rig file's `initial` in force -- once the runner has attached its store
([attach][flyball.rig.values.LiveValues.attach]). At start, a kept value is
restored when the rig file's `initial` for it is what it was when it was
written, and the unit the same; a changed `initial` means the file was edited
since, and the file wins (the row is forgotten); a changed unit raises
`value_not_restored` on the signal, and the file wins.

[source][flyball.rig.values.LiveValues.source] says, for the device page, where
the value in force came from: the rig file, restored (written by X at T), or
written in this run (by X at T).
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from flyball.foundation.device import Code, Sample, Severity, Signal, SubjectKind
from flyball.foundation.device.values import Values

if TYPE_CHECKING:
    from flyball.record import LiveValueRow
    from flyball.record.store import Store

    from .rig import Rig

log = logging.getLogger("flyball.values")

type Origin = Literal["rig_file", "restored", "written"]


@dataclass(frozen=True, slots=True)
class ValueSource:
    """Where a values signal's value in force came from."""

    origin: Origin
    """`rig_file` (its `initial`), `restored` (kept from an earlier run), `written` (this run)."""
    initial: Any
    """The rig file's `initial` in force."""
    writer: str | None = None
    """Who wrote the value in force, for `restored` and `written`; None when not known."""
    written_ns: int | None = None
    """When it was written, wall time in ns since the epoch."""


class LiveValues:
    """The rig's record of its values signals' writes: the source of each, and the store row."""

    def __init__(self, rig: Rig) -> None:
        self._rig = rig
        self.store: Store | None = None
        self._sources: dict[Signal, ValueSource] = {}

    def source(self, signal: Signal) -> ValueSource | None:
        """Where `signal`'s value in force came from; None if it is not a values signal."""
        device = signal.device
        if not isinstance(device, Values):
            return None
        if (held := self._sources.get(signal)) is not None:
            return held
        return ValueSource("rig_file", device.initial(str(signal.path)))

    def attach(self, store: Store) -> None:
        """Keep writes in `store` from now on, and restore what it kept for this rig's values.

        The runner's, once the store is open and the rig built, before it serves. The
        rig's latches attach here too (the runner's one store attach point): a latch an
        earlier run left re-applies its stop now.
        """
        try:
            self._attach(store)
        finally:
            self._rig.stopping.attach(store)

    def _attach(self, store: Store) -> None:
        self.store = store
        try:
            rows = {(r.device, r.signal): r for r in store.live_values() if r.kind == "value"}
        except Exception:
            log.exception("live values: the store could not be read; nothing restored")
            return
        for device in list(self._rig.devices.values()):
            if not isinstance(device, Values):
                continue
            for path, signal in device.signals.items():
                if (row := rows.get((device.name, path))) is not None:
                    self._restore(device, signal, row)

    def _restore(self, device: Values, signal: Signal, row: LiveValueRow) -> None:
        initial = device.initial(str(signal.path))
        unit = signal.unit.symbol or None
        store = self.store
        assert store is not None
        if row.initial != initial:
            with _logged("forget"):
                store.delete_live_value(device.name, str(signal.path))
            log.info(
                "%s: its initial changed (%r -> %r): the rig file's value",
                signal.address,
                row.initial,
                initial,
            )
            return
        if (row.unit or None) != unit:
            self._rig.conditions.set(
                signal,
                Code.VALUE_NOT_RESTORED,
                Severity.WARNING,
                f"{row.value!r} {row.unit or ''} was written by {row.writer or 'someone'}, but"
                f" the unit is now {unit or 'none'}: the rig file's {initial!r} is in force",
                {"value": row.value, "unit": row.unit, "now": unit},
            )
            return
        value = row.value
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            log.warning(
                "%s: kept value %r is not a finite number: not restored", signal.address, value
            )
            return
        self._sources[signal] = ValueSource("restored", initial, row.writer, row.written_ns)
        now = self._rig.clock.now_ns()
        self._rig.on_samples([Sample(signal.node, now, {signal: float(value)})])
        self._rig.event(
            Severity.INFO,
            SubjectKind.SIGNAL,
            signal.address,
            Code.VALUE_RESTORED,
            f"restored {value:g}, written by {row.writer or 'someone'}",
            {"value": value, "writer": row.writer, "written_ns": row.written_ns},
        )

    def written(self, signal: Signal, value: float | None, was: Any, writer: str | None) -> None:
        """A write of values signal `signal` committed `value`: log it, keep it, note its source.

        Under the rig's lock, in the commit. A store that fails is logged, not raised: the
        write has happened.
        """
        device = signal.device
        if not isinstance(device, Values) or value is None:
            return
        now = time.time_ns()
        initial = device.initial(str(signal.path))
        self._sources[signal] = ValueSource("written", initial, writer, now)
        self._rig.conditions.clear(signal, Code.VALUE_NOT_RESTORED, message="written again")
        unit = signal.unit.symbol or None
        self._rig.event(
            Severity.INFO,
            SubjectKind.SIGNAL,
            signal.address,
            Code.VALUE_WRITTEN,
            f"set to {value:g}{' ' + unit if unit else ''} by {writer or 'someone'}"
            + ("" if was is None else f" (was {was:g})"),
            {"value": value, "was": was, "writer": writer},
        )
        if (store := self.store) is None:
            return
        from flyball.record import LiveValueRow

        with _logged(f"keep {signal.address}"):
            head = store.head_rig_version()
            store.put_live_value(
                LiveValueRow(
                    device=device.name,
                    signal=str(signal.path),
                    kind="value",
                    value=value,
                    unit=unit,
                    initial=initial,
                    writer=writer,
                    written_ns=now,
                    head_version=None if head is None else head.id,
                )
            )

    def forget(self, signal: Signal) -> None:
        """A device went: drop what this run noted of its signal."""
        self._sources.pop(signal, None)


class _logged:
    """Log a store failure instead of raising it."""

    def __init__(self, what: str) -> None:
        self.what = what

    def __enter__(self) -> None:
        return None

    def __exit__(self, kind: Any, error: Any, traceback: Any) -> bool:
        if error is not None and isinstance(error, Exception):
            log.exception("live values: %s failed", self.what, exc_info=error)
            return True
        return False


__all__ = ["LiveValues", "ValueSource"]
