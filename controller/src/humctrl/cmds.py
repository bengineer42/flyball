from dataclasses import dataclass
from typing import Protocol

from humctrl.pumps import Flow


@dataclass(frozen=True, slots=True)
class Sleep:
    duration: float


@dataclass(frozen=True, slots=True)
class SetBlend:
    wet_fraction: float
    flow: Flow


@dataclass(frozen=True, slots=True)
class SetFlow:
    flow: Flow


@dataclass(frozen=True, slots=True)
class SetWetFraction:
    wet_fraction: float


@dataclass(frozen=True, slots=True)
class SetFlows:
    wet: float
    dry: float


@dataclass(frozen=True, slots=True)
class RegulateHumidity:
    humidity: float


@dataclass(frozen=True, slots=True)
class StopPumps: ...


@dataclass(frozen=True, slots=True)
class WaitForHumidity:
    @dataclass(frozen=True, slots=True)
    class Cross: ...

    @dataclass(frozen=True, slots=True)
    class Near: ...

    @dataclass(frozen=True, slots=True)
    class Stable:
        consecutive_readings: int
        within: float

    type Mode = Cross | Near | Stable

    humidity: float
    mode: Mode
    error: float
    timeout: float | None


@dataclass(frozen=True, slots=True)
class StartRecording:
    name: str | None


@dataclass(frozen=True, slots=True)
class StopRecording: ...


@dataclass(frozen=True, slots=True)
class AddFlag:
    flag: str


type ControlCommand = (
    Sleep
    | SetBlend
    | SetFlow
    | SetWetFraction
    | SetFlows
    | RegulateHumidity
    | StopPumps
    | WaitForHumidity
    | StartRecording
    | StopRecording
    | AddFlag
)


class ControlProgram(Protocol):
    cmds: list[ControlCommand]
    step: int
