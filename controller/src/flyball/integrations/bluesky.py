"""Run flyball devices inside a Bluesky plan.

Wraps a node's readings or one writable signal in Bluesky's duck-typed
*Readable* and *Movable* shapes without importing bluesky, so the `bluesky`
extra is only needed to run a plan:

    from bluesky import RunEngine
    from bluesky.plans import count
    RE = RunEngine()
    RE(count([NodeReadable(rig, rig.resolve("hum_sensors.dry"))], num=10))

Each publishing (`P`) signal under the node is one data key, named by its
address and described with the unit, shape and precision its spec carries. A
writable (`W`) signal is a *Movable* through
[SignalMovable][flyball.integrations.bluesky.SignalMovable].
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from flyball.core.signal import Access, Node, Signal
from flyball.core.trigger import Trigger
from flyball.runtime.rig import Rig

DataKey = dict[str, Any]
Value = dict[str, dict[str, Any]]


def _publishing(node: Node) -> list[Signal]:
    return [signal for signal in node.walk() if Access.P in signal.access]


def _describe(signal: Signal) -> DataKey:
    key: DataKey = {
        "source": f"flyball:{signal.address}",
        "dtype": "number",
        "shape": [],
        "units": signal.unit.symbol,
    }
    if signal.spec.precision is not None:
        key["precision"] = signal.spec.precision
    if signal.spec.range is not None:
        key["lower_ctrl_limit"], key["upper_ctrl_limit"] = signal.spec.range
    return key


class NodeReadable:
    """A device or namespace as a Bluesky *Readable*: one data key per publishing signal."""

    def __init__(self, rig: Rig, node: Node, name: str | None = None) -> None:
        self._rig = rig
        self._node = node
        self.name = name or node.address
        self.parent = None

    def describe(self) -> dict[str, DataKey]:
        return {signal.address: _describe(signal) for signal in _publishing(self._node)}

    def read(self) -> Value:
        values: Value = {}
        for signal in _publishing(self._node):
            if (reading := self._rig.latest.get(signal)) is not None:
                values[signal.address] = {"value": reading.value, "timestamp": reading.seconds}
        return values

    def describe_configuration(self) -> dict[str, DataKey]:
        return {}

    def read_configuration(self) -> Value:
        return {}


class Status:
    """A Bluesky *Status* over a [Trigger][flyball.core.trigger.Trigger].

    Done when it settles; success if it fired.
    """

    def __init__(self, signal: Trigger, timeout: float | None = None) -> None:
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


class SignalMovable:
    """One writable signal as a Bluesky *Movable*. `set` is immediate: its status is done."""

    def __init__(self, rig: Rig, signal: Signal, name: str | None = None) -> None:
        self._rig = rig
        self._signal = signal
        self.name = name or signal.address
        self.parent = None
        self._last: float | None = None

    def set(self, value: float) -> Status:
        self._rig.demand(self._signal.node, {self._signal: value})
        self._last = value
        signal = Trigger()
        signal.fire()
        return Status(signal)

    def describe(self) -> dict[str, DataKey]:
        return {
            self.name: {
                "source": f"flyball:{self.name}",
                "dtype": "number",
                "shape": [],
                "units": self._signal.unit.symbol,
            }
        }

    def read(self) -> Value:
        state = self._signal.device.written.get(self._signal)
        value = self._last if state is None else state.value
        return (
            {}
            if value is None
            else {self.name: {"value": value, "timestamp": self._rig.clock.now_s()}}
        )

    def describe_configuration(self) -> dict[str, DataKey]:
        return {}

    def read_configuration(self) -> Value:
        return {}
