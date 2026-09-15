"""A line as a device: an input read as 0/1, or an output switched by the demand."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from flyball.core.config import resolve
from flyball.core.device import DeviceConfig, DeviceState, command
from flyball.core.reading import Measurand, Reader, Sample, Source
from flyball.core.sink import Actuator, ActuatorState
from flyball.core.units.dimension import Unit
from pydantic import Field

from flyball_linux.links.gpio import GpioLink, GpioLinkConfig


@dataclass(frozen=True, slots=True, kw_only=True)
class GpioReaderState(DeviceState):
    level: bool | None = None


class GpioReader(Reader):
    """An input line, as a `level` measurand of 0 or 1."""

    def __init__(
        self,
        name: str,
        link: GpioLink,
        line: int,
        pull_up: bool | None = None,
        invert: bool = False,
        measurand: str = "level",
    ) -> None:
        self.link = link
        self.line = line
        self.invert = invert
        self.measurand = Measurand(measurand, Unit.get("1"), range=(0.0, 1.0), precision=0)
        self.source = Source(name, (self.measurand,))
        super().__init__(name, (self.source,))
        self._level: bool | None = None
        link.claim_input(line, pull_up)

    @property
    def state(self) -> GpioReaderState:
        return GpioReaderState(level=self._level)

    def read(self, time_ns: int) -> Iterable[Sample]:
        self._level = self.link.get(self.line) != self.invert
        return [
            Sample(
                self.source, self.source.next_seq(), time_ns, {self.measurand: float(self._level)}
            )
        ]


class GpioReaderConfig(DeviceConfig[GpioReader], tag="gpio_reader"):
    name: str
    link: GpioLinkConfig | str  # type: ignore[valid-type]
    line: int = Field(ge=0)
    pull_up: bool | None = None
    invert: bool = False
    measurand: str = "level"

    def build(self) -> GpioReader:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a chip before building")
        return GpioReader(
            self.name, resolve(self.link), self.line, self.pull_up, self.invert, self.measurand
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class GpioActuatorState(ActuatorState):
    on: bool = False


class GpioActuator(Actuator):
    """An output line: on when the demand reaches `threshold`. A relay, a valve, a fan.

    `threshold` is in `unit`, the loop's demand unit; the default is a
    dimensionless demand switching at 0.5. `on`/`off` override until the
    next demand.
    """

    def __init__(
        self,
        name: str,
        link: GpioLink,
        line: int,
        threshold: float = 0.5,
        invert: bool = False,
        initial: bool = False,
        unit: str = "1",
    ) -> None:
        super().__init__(name)
        self.link = link
        self.line = line
        self.threshold = threshold
        self.invert = invert
        self._demand: float | None = None
        self._on = initial
        link.claim_output(line, initial != invert)
        self.demand_unit = Unit.get(unit)  # type: ignore[misc]

    @property
    def state(self) -> GpioActuatorState:
        return GpioActuatorState(demand=self._demand, on=self._on)

    def _drive(self, on: bool) -> None:
        self._on = on
        self.link.set(self.line, on != self.invert)

    def set_demand(self, demand: float) -> float | None:
        self._demand = demand
        self._drive(demand >= self.threshold)
        return float(self._on)

    @command
    def on(self) -> GpioActuatorState:
        """Switch the line on, whatever the demand."""
        self._drive(True)
        return self.state

    @command
    def off(self) -> GpioActuatorState:
        """Switch the line off, whatever the demand."""
        self._drive(False)
        return self.state


class GpioActuatorConfig(DeviceConfig[GpioActuator], tag="gpio_actuator"):
    name: str
    link: GpioLinkConfig | str  # type: ignore[valid-type]
    line: int = Field(ge=0)
    threshold: float = 0.5
    invert: bool = Field(default=False, description="An active-low relay board.")
    initial: bool = False
    unit: str = Field(default="1", description="The loop's demand unit, which `threshold` is in.")

    def build(self) -> GpioActuator:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a chip before building")
        return GpioActuator(
            self.name,
            resolve(self.link),
            self.line,
            self.threshold,
            self.invert,
            self.initial,
            self.unit,
        )


__all__ = [
    "GpioActuator",
    "GpioActuatorConfig",
    "GpioActuatorState",
    "GpioReader",
    "GpioReaderConfig",
    "GpioReaderState",
]
