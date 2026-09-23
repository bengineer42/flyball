"""Use a PyMeasure instrument as a flyball device.

A PyMeasure driver's interface is `Instrument.measurement`, `.control` and
`.setting` properties whose docstrings usually name the unit in words. This
wrapper walks them, so one class serves every driver:

    devices:
      smu:
        driver: pymeasure
        instrument: pymeasure.instruments.keithley.Keithley2400
        adapter: "GPIB::24"
        channels:
          voltage: { property: voltage, publish: true }
          bias: { property: source_voltage }

Units come from the docstring by a word table (`volts` -> V), overridable
per channel. Nothing here imports pymeasure at module load: `PyMeasureConfig.build`
does, so the `pymeasure` extra is only needed where it is actually used.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any, Literal

from flyball.foundation.config import import_object
from flyball.foundation.device import (
    Access,
    Committable,
    DriverConfig,
    Node,
    Readable,
    Role,
    Sample,
    Signal,
    SignalSpec,
)
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.dimension import Unit
from flyball.foundation.quantities.errors import UnitNotFoundError
from flyball.foundation.quantities.si import One
from flyball.hardware.scan import Scan
from pydantic import BaseModel, ConfigDict, Field

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


class PyMeasureSignal(BaseModel):
    """One line of a `pymeasure` device's tree: one property, wrapped as a signal.

    A property with a setter is a demand (`RPW`: its readback is the value
    last set, whether or not the property also has a getter); a
    getter-only one is an output, readable on demand. `publish` streams a
    getter-only one too, and needs a getter -- see
    [PyMeasure][flyball_pymeasure.PyMeasure].
    """

    model_config = ConfigDict(extra="forbid")

    property: str = Field(description="The instrument's attribute name.")
    unit: str | None = Field(default=None, description="Overrides the docstring's unit.")
    publish: bool = False
    role: Literal["setting"] | None = Field(
        default=None,
        description=(
            "`setting`: a writable entry that changes how the instrument behaves (a range,"
            " a frequency, a configuration register), not what controls the process; a"
            " controller cannot drive it. Omitted: a writable entry is a demand."
        ),
    )


class PyMeasure(Readable, Committable):
    """Chosen properties of a PyMeasure instrument as one device.

    Args:
        name: The device's name.
        instrument: A PyMeasure `Instrument`.
        channels: `{name: PyMeasureSignal}` -- which properties to expose, and how.

    Raises:
        ValueError: A channel's property does not exist on `instrument`, or
            is neither readable nor writable, or `publish` without a getter.
    """

    blocking = True  # writes go to a bus: the rig queues them off the loop's thread

    def __init__(
        self,
        name: str,
        instrument: Any,
        channels: Mapping[str, PyMeasureSignal],
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.instrument = instrument
        self.channels = dict(channels)
        self._gettable: dict[str, bool] = {}
        self._scan = Scan()
        available = properties(instrument)
        tree: list[SignalSpec] = []
        for key, channel in self.channels.items():
            prop = available.get(channel.property)
            if prop is None:
                raise ValueError(
                    f"{type(instrument).__name__} has no property {channel.property!r}"
                )
            gettable, settable = prop.fget is not None, prop.fset is not None
            if not gettable and not settable:
                raise ValueError(f"{key!r}: {channel.property} is neither readable nor writable")
            if channel.publish and not gettable:
                raise ValueError(f"{key!r}: publish needs a readable property")
            if channel.role is not None and not settable:
                raise ValueError(f"{key!r}: only a settable property can be declared a setting")
            self._gettable[key] = gettable
            if settable:
                role = Role.SETTING if channel.role == "setting" else Role.DEMAND
                access = Access.RPW
            else:
                role, access = Role.READOUT, (Access.RP if channel.publish else Access.R)
            unit = Unit.get(channel.unit) if channel.unit else unit_from_doc(prop.__doc__)
            tree.append(
                SignalSpec(name=key, quantity=Quantity(key, unit), access=access, role=role)
            )
        self.bind(tree)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """One read per due, published, actually-readable channel under `node`.

        Walks `channels`, not the tree: `conditions` and any `last.*` are in
        every device's tree now, and neither has a property behind it. A
        demand whose property has no getter is skipped here -- its reading
        is the value last committed, not a poll.
        """
        target = node if node is not None else self.root
        candidates = {
            self.signals[key]: key
            for key in self.channels
            if self._gettable[key] and target.contains(self.signals[key])
        }
        for signal in self._scan.due(candidates, time_ns, whole=False):
            value = getattr(self.instrument, self.channels[candidates[signal]].property)
            yield Sample(self.root, time_ns, {signal: float(value)})

    def write_signal(self, signal: Signal, value: float) -> None:
        setattr(self.instrument, self.channels[signal.name].property, value)


class PyMeasureConfig(DriverConfig[PyMeasure], type="pymeasure"):
    """`driver: pymeasure`. `channels` is the driver's own tree -- see `PyMeasureSignal`.

    Named `channels`, not `signals`: the envelope's `signals:` key is
    reserved for signal metadata, the same for every driver.
    """

    instrument: str = Field(
        description="Dotted class, e.g. pymeasure.instruments.keithley.Keithley2400"
    )
    adapter: str = Field(description="The resource: 'GPIB::24', 'ASRL/dev/ttyUSB0', a VISA string.")
    kwargs: dict[str, Any] = Field(default_factory=dict)
    channels: dict[str, PyMeasureSignal]

    def build(self, name: str, label: str | None = None) -> PyMeasure:
        instrument = import_object(self.instrument)(self.adapter, **self.kwargs)
        return PyMeasure(name, instrument, self.channels, label=label)


__all__ = [
    "PyMeasure",
    "PyMeasureConfig",
    "PyMeasureSignal",
    "properties",
    "unit_from_doc",
]
