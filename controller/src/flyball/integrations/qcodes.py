"""Use a QCoDeS instrument as a flyball device.

A QCoDeS `Instrument` carries `parameters`, each with a `unit`, a `label`,
`get` and/or `set` -- what a signal needs, so one wrapper serves every
driver:

    devices:
      smu:
        driver: qcodes
        instrument: qcodes.instrument_drivers.Keithley.Keithley2450
        channels:
          voltage: { property: source.voltage, publish: true }

Nothing here imports qcodes at module load: `QCoDeSConfig.build` does, so the
`qcodes` extra is only needed where it is actually used. A fake with the
same attributes drives the tests.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from flyball.core.config import import_object
from flyball.core.device import Device, DriverConfig
from flyball.core.quantity import Quantity
from flyball.core.signal import Access, Node, Sample, Signal, SignalSpec
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


def _parameter(instrument: Any, dotted: str) -> Any:
    """The parameter or sub-instrument attribute named by a dotted path: `"source.voltage"`."""
    target = instrument
    for part in dotted.split("."):
        target = (
            target.parameters[part]
            if part in getattr(target, "parameters", {})
            else getattr(target, part)
        )
    return target


class QCoDeSSignal(BaseModel):
    """One line of a `qcodes` device's tree: one parameter, wrapped as a signal.

    A settable parameter is writable; a gettable one is readable on demand.
    `publish` also streams it, and needs a getter -- see
    [QCoDeS][flyball.integrations.qcodes.QCoDeS].
    """

    model_config = ConfigDict(extra="forbid")

    property: str = Field(
        description="The parameter's name, dotted for a submodule: 'source.voltage'."
    )
    unit: str | None = Field(default=None, description="Overrides the parameter's own `unit`.")
    publish: bool = False


class QCoDeS(Device):
    """Chosen parameters of a QCoDeS instrument as one device.

    Args:
        name: The device's name.
        instrument: A QCoDeS `Instrument` (or anything with `parameters`).
        channels: `{name: QCoDeSSignal}` -- which parameters to expose, and how.

    Raises:
        ValueError: A channel's parameter is neither gettable nor settable,
            or `publish` without a getter.
    """

    blocking = True  # writes go to a bus: the rig queues them off the loop's thread

    def __init__(
        self,
        name: str,
        instrument: Any,
        channels: Mapping[str, QCoDeSSignal],
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.instrument = instrument
        self.channels = dict(channels)
        self._parameters: dict[str, Any] = {}
        self._last_read: dict[Signal, int] = {}
        tree: list[SignalSpec] = []
        for key, channel in self.channels.items():
            parameter = _parameter(instrument, channel.property)
            self._parameters[key] = parameter
            gettable, settable = _gettable(parameter), _settable(parameter)
            access = Access(0)
            if gettable:
                access |= Access.R
            if settable:
                access |= Access.W
            if channel.publish:
                if not gettable:
                    raise ValueError(f"{key!r}: publish needs a gettable parameter")
                access |= Access.P
            if not access:
                raise ValueError(f"{key!r}: {channel.property} is neither gettable nor settable")
            unit = (
                Unit.get(channel.unit)
                if channel.unit
                else unit_for(getattr(parameter, "unit", None))
            )
            tree.append(SignalSpec(name=key, quantity=Quantity(key, unit), access=access))
        self.bind(tree)

    def _due(self, signal: Signal, time_ns: int) -> bool:
        poll_s = signal.poll_s
        if poll_s is None:
            return True
        last = self._last_read.get(signal)
        return last is None or (time_ns - last) >= poll_s * 1e9

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        target = node if node is not None else self.root
        for signal in target.walk():
            if Access.P not in signal.access or not self._due(signal, time_ns):
                continue
            value = self._parameters[signal.name].get()
            self._last_read[signal] = time_ns
            yield Sample(self.root, time_ns, {signal: float(value)})

    def write_signal(self, signal: Signal, value: float) -> None:
        self._parameters[signal.name].set(value)


class QCoDeSConfig(DriverConfig[QCoDeS], tag="qcodes"):
    """`driver: qcodes`. `channels` is the driver's own tree -- see `QCoDeSSignal`.

    Named `channels`, not `signals`: the envelope's `signals:` key is
    reserved for overrides, the same for every driver.
    """

    instrument: str = Field(
        description="Dotted class, e.g. qcodes.instrument_drivers.Keithley.Keithley2450"
    )
    instrument_name: str | None = Field(
        default=None, description="The QCoDeS instrument's own `name`; defaults to the device's."
    )
    args: list[Any] = Field(default_factory=list)
    kwargs: dict[str, Any] = Field(default_factory=dict)
    channels: dict[str, QCoDeSSignal]

    def build(self, name: str, label: str | None = None) -> QCoDeS:
        instrument = import_object(self.instrument)(
            self.instrument_name or name, *self.args, **self.kwargs
        )
        return QCoDeS(name, instrument, self.channels, label=label)


__all__ = ["QCoDeS", "QCoDeSConfig", "QCoDeSSignal", "bounds", "unit_for"]
