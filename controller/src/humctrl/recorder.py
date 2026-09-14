from __future__ import annotations

from enum import Enum
from typing import Any, Protocol

from humctrl.control.loop import LoopMode
from humctrl.control.types import ControllerState
from humctrl.core import Percent
from humctrl.humidity.readers import HTReading, HTReadings, Reader
from humctrl.pumps import PumpsState, SupplyFlows
from humctrl.state import State


class SpanKind(Enum):
    PROGRAM = "PROGRAM"
    RUN = "RUN"
    COMMAND = "COMMAND"
    NOTE = "NOTE"


class Session:
    id: int
    start: int
    start_ns: int
    duration_ns: int | None = None
    version: str | None = None
    details: Any | None = None
    config: Any | None = None
    hardware: Any | None = None


class Span:
    id: int
    parent: int | None = None
    kind: SpanKind
    label: str
    start_ns: int  # start time in nanoseconds after session start
    end_ns: int | None = None  # end time in nanoseconds after session start
    details: Any | None = None


class Sample:
    id: int
    offset_ns: int
    reading: HTReadings
    pumps: PumpsState | None = None
    target_humidity: Percent | None = None
    dry_humidity: Percent | None = None
    wet_humidity: Percent | None = None
    controller: ControllerState | None = None


class Tick:
    loop: str
    time_ns: int
    mode: LoopMode
    reading: float | None
    setpoint: float | None = None
    correction: float
    demand: float | None = None
    expected: float | None = None
    delivered_correction: float | None = None


class Recorder(Protocol):
    def record(
        self,
        time_ns: int,
        reading: Reader | None = None,
        flows: SupplyFlows | None = None,
        target_humidity: Percent | None = None,
    ) -> None: ...
    def record_state(
        self,
        state: State,
    ) -> None: ...
    def add_flag(self, flag: str, time: float) -> None: ...


class SessionStore(Protocol):
    session_id: int
    _ended: bool = False

    def write_sample(self, sample: Sample) -> None: ...
    def write_span(self, span: Span) -> None: ...
    def read_spans(self, session_id: int) -> list[Span]: ...
    def end(self) -> None: ...


class Store(Protocol):
    def write_state(self, state: State) -> None: ...
    def write_reading(self, reading: HTReading) -> None: ...
    def write_session(self) -> None: ...
