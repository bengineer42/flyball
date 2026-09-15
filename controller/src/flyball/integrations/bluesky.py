"""Run flyball devices inside a Bluesky plan.

Wraps a [Source][flyball.core.reading.Source], the rig, or an actuator
command in Bluesky's duck-typed *Readable* and *Movable* shapes without
importing bluesky, so the `bluesky` extra is only needed to run a plan:

    from bluesky import RunEngine
    from bluesky.plans import count
    RE = RunEngine()
    RE(count([SourceReadable(rig, chamber)], num=10))

Each channel is one data key named `<source>.<measurand>`, described with the
dtype, shape, units and precision the Measurand carries.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from flyball.core.reading import Channel, Reading, Sample, Source
from flyball.core.signal import Signal
from flyball.core.sink import Actuator
from flyball.runtime.rig import Rig

DataKey = dict[str, Any]
Value = dict[str, dict[str, Any]]


def _key(channel: Channel) -> str:
    return f"{channel.source.name}.{channel.measurand.name}"


def _describe(channel: Channel) -> DataKey:
    m = channel.measurand
    key: DataKey = {
        "source": f"flyball:{_key(channel)}",
        "dtype": "number",
        "shape": [],
        "units": m.unit.symbol,
    }
    if m.precision is not None:
        key["precision"] = m.precision
    if m.range is not None:
        key["lower_ctrl_limit"], key["upper_ctrl_limit"] = m.range
    return key


def _value(reading: Reading) -> dict[str, Any]:
    return {"value": reading.value, "timestamp": reading.time_ns / 1e9}


class SourceReadable:
    """One source as a Bluesky *Readable*: its latest sample, one data key per channel."""

    def __init__(self, rig: Rig, source: Source, name: str | None = None) -> None:
        self._rig = rig
        self._source = source
        self.name = name or str(source.name)
        self.parent = None

    def describe(self) -> dict[str, DataKey]:
        return {_key(ch): _describe(ch) for ch in self._source.channels}

    def read(self) -> Value:
        sample: Sample | None = self._rig._samples.get(self._source)
        if sample is None:
            return {}
        return {_key(self._source[m]): _value(sample.reading(m)) for m in sample.values}

    def describe_configuration(self) -> dict[str, DataKey]:
        return {}

    def read_configuration(self) -> Value:
        return {}


class Status:
    """A Bluesky *Status* over a [Signal][flyball.core.signal.Signal].

    Done when it settles; success if it fired.
    """

    def __init__(self, signal: Signal, timeout: float | None = None) -> None:
        self._signal = signal
        self._callbacks: list[Callable[[Status], None]] = []
        self._lock = threading.Lock()
        self._notified = False
        self._finished = threading.Event()  # set once every callback has run
        threading.Thread(target=self._watch, args=(timeout,), daemon=True).start()

    def _watch(self, timeout: float | None) -> None:
        if not self._signal.wait(timeout):
            self._signal.expire()
        with self._lock:
            self._notified = True
            callbacks = list(self._callbacks)
        try:
            for callback in callbacks:
                callback(self)
        finally:
            self._finished.set()

    @property
    def done(self) -> bool:
        return self._signal.settled

    @property
    def success(self) -> bool:
        return self._signal.fired

    def exception(self, timeout: float | None = None) -> Exception | None:
        self._signal.wait(timeout)
        if self._signal.fired:
            return None
        return TimeoutError("timed out") if self._signal.timed_out else RuntimeError("interrupted")

    def add_callback(self, callback: Callable[[Status], None]) -> None:
        with self._lock:
            if not self._notified:
                self._callbacks.append(callback)
                return
        callback(self)

    def wait(self, timeout: float | None = None) -> None:
        """Block until done and every callback has run; raise if it did not succeed."""
        if not self._finished.wait(timeout):
            raise TimeoutError("status did not complete in time")
        if (error := self.exception(0)) is not None:
            raise error


class DemandMovable:
    """An actuator's demand as a Bluesky *Movable*. `set` is immediate: its status is done."""

    def __init__(self, rig: Rig, actuator: Actuator, name: str | None = None) -> None:
        self._rig = rig
        self._actuator = actuator
        self.name = name or actuator.name
        self.parent = None
        self._last: float | None = None

    def set(self, value: float) -> Status:
        self._actuator.set_demand(value)
        self._rig.apply(self._actuator)
        self._last = value
        signal = Signal()
        signal.fire()
        return Status(signal)

    def describe(self) -> dict[str, DataKey]:
        unit = type(self._actuator).demand_unit
        return {
            f"{self.name}.demand": {
                "source": f"flyball:{self.name}.demand",
                "dtype": "number",
                "shape": [],
                "units": "" if unit is None else unit.symbol,
            }
        }

    def read(self) -> Value:
        demand = self._actuator.state.demand
        if demand is None:
            demand = self._last
        return (
            {}
            if demand is None
            else {f"{self.name}.demand": {"value": demand, "timestamp": self._rig.clock.now_s()}}
        )

    def describe_configuration(self) -> dict[str, DataKey]:
        return {}

    def read_configuration(self) -> Value:
        return {}
