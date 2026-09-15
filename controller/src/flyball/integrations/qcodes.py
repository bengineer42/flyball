"""Use a QCoDeS instrument as a flyball reader or actuator.

A QCoDeS `Instrument` carries `parameters`, each with a `unit`, a `label`,
`get` and/or `set`, and `vals` bounds -- what a measurand and a command
need, so one wrapper serves every driver:

    from qcodes.instrument_drivers.Keithley import Keithley2450
    smu = Keithley2450("smu", "TCPIP::192.168.1.5::INSTR")
    rig.start_reader(QCoDeSReader("smu", smu), period=1.0)
    rig.attach_loop(..., QCoDeSActuator("bias", smu.source.voltage))

Nothing here imports qcodes; a fake with the same attributes drives the
tests. The instrument keeps its own connection; flyball's links are not
involved.

"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field

from flyball.core.config import Config, import_object, resolve
from flyball.core.device import DeviceConfig, DeviceState, command
from flyball.core.reading import Measurand, Reader, Sample, Source
from flyball.core.sink import Actuator, ActuatorState
from flyball.core.units.dimension import Unit
from flyball.core.units.errors import UnitNotFoundError
from flyball.core.units.si import One


def unit_for(symbol: str | None, overrides: Mapping[str, str] = {}) -> Unit:
    """The unit a parameter reports in, or dimensionless when it says nothing we know."""
    symbol = overrides.get(symbol or "", symbol)
    if not symbol:
        return One
    try:
        return Unit.get(symbol)
    except UnitNotFoundError:
        return One


def bounds(parameter: Any) -> tuple[float, float] | None:
    """`(min, max)` from a `Numbers` validator, if the parameter has one."""
    vals = getattr(parameter, "vals", None)
    lo = getattr(vals, "min_value", getattr(vals, "_min_value", None))
    hi = getattr(vals, "max_value", getattr(vals, "_max_value", None))
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
        finite = (lo, hi) if abs(lo) < 1e300 and abs(hi) < 1e300 else None
        return finite
    return None


def _gettable(parameter: Any) -> bool:
    return bool(getattr(parameter, "gettable", hasattr(parameter, "get")))


def _settable(parameter: Any) -> bool:
    return bool(getattr(parameter, "settable", hasattr(parameter, "set")))


@dataclass(frozen=True, slots=True, kw_only=True)
class QCoDeSState(DeviceState):
    values: dict[str, float] = field(default_factory=dict)


class QCoDeSReader(Reader):
    """Every numeric, gettable parameter of an instrument as one source.

    Args:
        name: The reader's name, and the source's.
        instrument: A QCoDeS `Instrument` (or anything with `parameters`).
        parameters: Which to read; default every gettable one except `IDN`.
        units: Overrides for parameters whose `unit` the table does not know.
    """

    def __init__(
        self,
        name: str,
        instrument: Any,
        parameters: Iterable[str] | None = None,
        units: Mapping[str, str] = {},
    ) -> None:
        self.instrument = instrument
        available: dict[str, Any] = dict(instrument.parameters)
        chosen = (
            list(parameters)
            if parameters is not None
            else [key for key, p in available.items() if key != "IDN" and _gettable(p)]
        )
        self.parameters = {key: available[key] for key in chosen}
        self.measurands = {
            key: Measurand(
                f"{name}.{key}",
                unit_for(getattr(p, "unit", None), units),
                getattr(p, "label", "") or key,
                bounds(p),
            )
            for key, p in self.parameters.items()
        }
        self.source = Source(name, self.measurands.values())
        super().__init__(name, (self.source,))
        self._values: dict[str, float] = {}

    @property
    def state(self) -> QCoDeSState:
        return QCoDeSState(values=dict(self._values))

    def read(self, time_ns: int) -> Iterable[Sample]:
        values: dict[Measurand, float] = {}
        for key, parameter in self.parameters.items():
            value = parameter.get()
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values[self.measurands[key]] = float(value)
        self._values = {m.name: v for m, v in values.items()}
        return [Sample(self.source, self.source.next_seq(), time_ns, values)]

    @command
    def set(self, parameter: str, value: float) -> float | None:
        """Set any settable parameter by name; returns its readback if it has one."""
        target = self.instrument.parameters[parameter]
        if not _settable(target):
            raise ValueError(f"{parameter!r} is not settable")
        target.set(value)
        return float(target.get()) if _gettable(target) else None


@dataclass(frozen=True, slots=True, kw_only=True)
class QCoDeSActuatorState(ActuatorState):
    readback: float | None = None


class QCoDeSActuator(Actuator):
    """One settable parameter as the loop's actuator."""

    def __init__(self, name: str, parameter: Any, unit: str | None = None) -> None:
        super().__init__(name)
        if not _settable(parameter):
            raise TypeError(f"{getattr(parameter, 'name', parameter)!r} cannot be set")
        self.parameter = parameter
        self._demand: float | None = None
        self._readback: float | None = None
        symbol = unit or getattr(parameter, "unit", None)
        if symbol:
            self.demand_unit = unit_for(symbol)  # type: ignore[misc]

    @property
    def state(self) -> QCoDeSActuatorState:
        return QCoDeSActuatorState(demand=self._demand, readback=self._readback)

    def set_demand(self, demand: float) -> float | None:
        self._demand = demand
        self.parameter.set(demand)
        if _gettable(self.parameter):
            self._readback = float(self.parameter.get())
            return self._readback
        return None


# region In a rig file


class QCoDeSInstrumentConfig(Config[Any], tag="qcodes"):
    """A QCoDeS instrument, built once and shared by the devices that name it.

    Goes under `links` in a rig file; readers and actuators refer to it by
    name. `driver` is the dotted path of the driver class, `args` and
    `kwargs` whatever its constructor takes beyond its name.
    """

    driver: str = Field(description="e.g. qcodes.instrument_drivers.Keithley.Keithley2450")
    name: str
    args: list[Any] = Field(default_factory=list)
    kwargs: dict[str, Any] = Field(default_factory=dict)

    def build(self) -> Any:
        return import_object(self.driver)(self.name, *self.args, **self.kwargs)


class QCoDeSReaderConfig(DeviceConfig[QCoDeSReader], tag="qcodes_reader"):
    name: str
    link: QCoDeSInstrumentConfig | str
    parameters: list[str] | None = None
    units: dict[str, str] = Field(default_factory=dict)

    def build(self) -> QCoDeSReader:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to an instrument before building")
        return QCoDeSReader(self.name, resolve(self.link), self.parameters, self.units)


class QCoDeSActuatorConfig(DeviceConfig[QCoDeSActuator], tag="qcodes_actuator"):
    name: str
    link: QCoDeSInstrumentConfig | str
    parameter: str = Field(description="The settable parameter, e.g. 'volt' or 'source.voltage'.")
    unit: str | None = None

    def build(self) -> QCoDeSActuator:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to an instrument before building")
        instrument = resolve(self.link)
        target = instrument
        for part in self.parameter.split("."):  # submodules: source.voltage
            target = (
                target.parameters[part]
                if part in getattr(target, "parameters", {})
                else getattr(target, part)
            )
        return QCoDeSActuator(self.name, target, self.unit)


# endregion
