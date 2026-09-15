"""Use a PyMeasure instrument as a flyball reader or actuator.

A PyMeasure driver's interface is `Instrument.measurement`, `.control` and
`.setting` properties whose docstrings usually name the unit in words. This
wrapper walks them, so one class serves every driver:

    from pymeasure.instruments.keithley import Keithley2400
    smu = Keithley2400("GPIB::24")
    rig.start_reader(PyMeasureReader("smu", smu, ["voltage", "current"]), period=1.0)
    rig.attach_loop(..., PyMeasureActuator("bias", smu, "source_voltage"))

Units come from the docstring by a word table (`volts` -> V), overridable
per attribute. Nothing here imports pymeasure.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field

from flyball.core.config import Config, import_object, resolve
from flyball.core.device import DeviceConfig, DeviceState
from flyball.core.reading import Measurand, Reader, Sample, Source
from flyball.core.sink import Actuator, ActuatorState
from flyball.core.units.dimension import Unit
from flyball.core.units.errors import UnitNotFoundError
from flyball.core.units.si import One

WORDS: dict[str, str] = {
    "volts": "V",
    "volt": "V",
    "millivolts": "mV",
    "amps": "A",
    "amperes": "A",
    "milliamps": "mA",
    "microamps": "µA",
    "ohms": "Ω",
    "watts": "W",
    "hertz": "Hz",
    "hz": "Hz",
    "khz": "kHz",
    "mhz": "MHz",
    "seconds": "s",
    "milliseconds": "ms",
    "minutes": "min",
    "kelvin": "K",
    "celsius": "°C",
    "degrees c": "°C",
    "degrees celsius": "°C",
    "percent": "%",
    "%": "%",
    "bar": "bar",
    "mbar": "mbar",
    "torr": "Torr",
    "pa": "Pa",
    "kpa": "kPa",
    "sccm": "sccm",
    "slm": "L/min",
    "ml/min": "mL/min",
    "l/min": "L/min",
    "mm": "mm",
    "meters": "m",
    "metres": "m",
    "degrees": "°",
    "nm": "nm",
}
"""Words PyMeasure docstrings use for units, to a symbol the table knows."""

_IN_UNIT = re.compile(r"\bin\s+([A-Za-z°µ/%]+(?:\s+[cC])?)")


def unit_from_doc(doc: str | None) -> Unit:
    """The unit a docstring names after `in`: `"the voltage, in volts"` -> V."""
    if not doc:
        return One
    match = _IN_UNIT.search(doc)
    if match is None:
        return One
    word = match.group(1).lower().rstrip(".,;")
    symbol = WORDS.get(word, word)
    try:
        return Unit.get(symbol)
    except UnitNotFoundError:
        return One


def properties(instrument: Any) -> dict[str, property]:
    """Every `property` on the instrument's class, base classes included."""
    found: dict[str, property] = {}
    for klass in reversed(type(instrument).__mro__):
        for key, value in vars(klass).items():
            if isinstance(value, property) and not key.startswith("_"):
                found[key] = value
    return found


@dataclass(frozen=True, slots=True, kw_only=True)
class PyMeasureState(DeviceState):
    values: dict[str, float] = field(default_factory=dict)


class PyMeasureReader(Reader):
    """Chosen readable properties of an instrument as one source.

    Args:
        name: The reader's name, and the source's.
        instrument: A PyMeasure `Instrument`.
        measurements: Which properties to read. Required: reading every
            property would be slow and side-effecting.

        units: Overrides where the docstring does not say, keyed by property.
    """

    def __init__(
        self,
        name: str,
        instrument: Any,
        measurements: Iterable[str],
        units: Mapping[str, str] = {},
    ) -> None:
        self.instrument = instrument
        available = properties(instrument)
        self.attributes = list(measurements)
        missing = [a for a in self.attributes if a not in available or available[a].fget is None]
        if missing:
            raise ValueError(f"{type(instrument).__name__} has no readable {missing}")
        self.measurands = {
            key: Measurand(
                f"{name}.{key}",
                Unit.get(units[key]) if key in units else unit_from_doc(available[key].__doc__),
                key.replace("_", " "),
            )
            for key in self.attributes
        }
        self.source = Source(name, self.measurands.values())
        super().__init__(name, (self.source,))
        self._values: dict[str, float] = {}

    @property
    def state(self) -> PyMeasureState:
        return PyMeasureState(values=dict(self._values))

    def read(self, time_ns: int) -> Iterable[Sample]:
        values: dict[Measurand, float] = {}
        for key in self.attributes:
            value = getattr(self.instrument, key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values[self.measurands[key]] = float(value)
        self._values = {m.name: v for m, v in values.items()}
        return [Sample(self.source, self.source.next_seq(), time_ns, values)]


@dataclass(frozen=True, slots=True, kw_only=True)
class PyMeasureActuatorState(ActuatorState):
    readback: float | None = None


class PyMeasureActuator(Actuator):
    """One writable property (a `control` or `setting`) as the loop's actuator."""

    def __init__(self, name: str, instrument: Any, attribute: str, unit: str | None = None) -> None:
        super().__init__(name)
        prop = properties(instrument).get(attribute)
        if prop is None or prop.fset is None:
            raise TypeError(f"{type(instrument).__name__}.{attribute} is not writable")
        self.instrument = instrument
        self.attribute = attribute
        self._readable = prop.fget is not None
        self._demand: float | None = None
        self._readback: float | None = None
        found = Unit.get(unit) if unit else unit_from_doc(prop.__doc__)
        if found is not One:
            self.demand_unit = found  # type: ignore[misc]

    @property
    def state(self) -> PyMeasureActuatorState:
        return PyMeasureActuatorState(demand=self._demand, readback=self._readback)

    def set_demand(self, demand: float) -> float | None:
        self._demand = demand
        setattr(self.instrument, self.attribute, demand)
        if self._readable:
            self._readback = float(getattr(self.instrument, self.attribute))
            return self._readback
        return None


# region In a rig file


class PyMeasureInstrumentConfig(Config[Any], tag="pymeasure"):
    """A PyMeasure instrument, built once and shared. `driver` is the class's dotted path."""

    driver: str = Field(description="e.g. pymeasure.instruments.keithley.Keithley2400")
    adapter: str = Field(description="The resource: 'GPIB::24', 'ASRL/dev/ttyUSB0', a VISA string.")
    kwargs: dict[str, Any] = Field(default_factory=dict)

    def build(self) -> Any:
        return import_object(self.driver)(self.adapter, **self.kwargs)


class PyMeasureReaderConfig(DeviceConfig[PyMeasureReader], tag="pymeasure_reader"):
    name: str
    link: PyMeasureInstrumentConfig | str
    measurements: list[str]
    units: dict[str, str] = Field(default_factory=dict)

    def build(self) -> PyMeasureReader:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to an instrument before building")
        return PyMeasureReader(self.name, resolve(self.link), self.measurements, self.units)


class PyMeasureActuatorConfig(DeviceConfig[PyMeasureActuator], tag="pymeasure_actuator"):
    name: str
    link: PyMeasureInstrumentConfig | str
    attribute: str
    unit: str | None = None

    def build(self) -> PyMeasureActuator:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to an instrument before building")
        return PyMeasureActuator(self.name, resolve(self.link), self.attribute, self.unit)


# endregion
