from dataclasses import dataclass

from humctrl.blender import BlenderSpec, BlenderState, BlenderView
from humctrl.clock import Time
from humctrl.control import (
    ControllerView,
)
from humctrl.control.types import ControlLawConfig
from humctrl.readers import HTReadings
from humctrl.typing import Positive


@dataclass(slots=True, frozen=True)
class State:
    running: bool
    duration_ns: int
    controller: ControllerView
    pumps: BlenderState | None = None
    readings: HTReadings | None = None
    recording: bool | None = None


@dataclass(slots=True, frozen=True)
class Spec:
    start: Time
    process_interval: Positive
    controller: ControlLawConfig
    pumps: BlenderSpec | None = None


@dataclass(slots=True, frozen=True)
class View:
    start: Time
    duration_ns: int
    running: bool
    process_interval: Positive
    controller: ControllerView
    pumps: BlenderView | None = None
    readings: HTReadings | None = None
    recording: bool | None = None
