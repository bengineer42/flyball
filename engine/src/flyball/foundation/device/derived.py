"""Derived devices: readouts computed from other devices' signals, and `driver: curve`.

```yaml
devices:
  adc:
    driver: ads1115
    channels: { raw_v: { channel: 0, unit: V } }
    signals: { raw_v: { record: false } }
  turbidity:
    driver: curve
    inputs: { x: adc.raw_v }
    curve: { type: table, points: [[2.5, 3000], [3.5, 1000], [4.2, 0]] }
    unit: NTU
```

A [Derived][flyball.foundation.device.derived.Derived] device reads nothing and
has no demands: it computes its readouts from its inputs in
`inputs_changed`, within the delivery that brought them, so a controller
measuring one steps on it in that delivery. An input with no value gives the
outputs its no-value (value-health §4: a pointwise operator propagates it);
nothing is substituted. A stop has nothing to do to it.

[Curve][flyball.foundation.device.derived.Curve] is the first: one input `x`,
one readout `value`, a [Linear][flyball.foundation.quantities.curves.Linear] or
[Table][flyball.foundation.quantities.curves.Table] between them. Beyond the
table's domain there is no value, `invalid("out_of_domain")` with the side it
fell off: a calibration is never extrapolated, and never held flat at its
end. Its output is the engineering value of the signal it follows, which the
wire says on both (`raw`, `raw_for`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..errors import NotReadyError
from ..quantities import Unit
from ..quantities.curves import Linear, Table
from ..quantities.errors import UnitNotFoundError
from ..quantities.quantity import Quantity
from ..quantities.si import Unitless
from .binding import InputBinding, values_of
from .descriptors import Input
from .device import Device, DriverConfig
from .novalue import NoValue, NoValueError, invalid
from .signal import Access, Role, Sample, Signal, SignalSpec, Value


class Derived(Device):
    """Readouts computed from its inputs as they change: nothing read, nothing demanded.

    A subclass declares its inputs and binds its readouts, and computes them in
    [compute][flyball.foundation.device.derived.Derived.compute] from its inputs'
    values; the base reads the inputs, and pushes the worst input's no-value on
    every readout while one has none (`pending` pushes nothing).
    """

    def compute(self, values: Mapping[str, Value]) -> Mapping[Signal, Value]:
        """Each readout's value from the inputs' `values`, by input name.

        A `NoValue` for a readout it cannot give (`invalid(reason)`).
        """
        raise NotImplementedError

    def raw_of(self, signal: Signal) -> Signal | None:
        """The signal whose engineering value `signal` is, if it is one: a calibration's source.

        What the wire pairs as raw and engineering (`raw`, `raw_for`). Default: None,
        for an operator that is not a calibration of one signal (a difference, a rate).
        """
        return None

    def inputs_changed(self, time_ns: int, changed: list[InputBinding]) -> None:
        try:
            values = dict(zip(self.bound, values_of(*self.bound.values()), strict=True))
        except NoValueError as error:
            outputs: Mapping[Signal, Value] = dict.fromkeys(self.published.values(), error.no_value)
        except NotReadyError:
            return
        else:
            outputs = self.compute(values)
        if outputs:
            self.router.push(Sample(self.root, time_ns, dict(outputs)))


# region driver: curve


class LinearCurve(BaseModel):
    """`scale * x + offset`, everywhere `x` is finite."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["linear"]
    scale: float = Field(description="Output units per input unit.")
    offset: float = Field(default=0.0, description="The output at `x` = 0.")

    def build(self) -> Linear:
        return Linear(self.scale, self.offset)

    @model_validator(mode="after")
    def _builds(self) -> LinearCurve:
        self.build()  # a non-finite scale or offset is refused at load
        return self


class TableCurve(BaseModel):
    """Piecewise-linear `[x, y]` breakpoints; no value outside the first and last `x`."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["table"]
    points: list[tuple[float, float]] = Field(
        description="`[x, y]` pairs, in any order: at most 1024, each finite. Two sharing an `x`"
        " are a step, the smaller `y` taken at it."
    )

    def build(self) -> Table:
        return Table(tuple(self.points))

    @model_validator(mode="after")
    def _builds(self) -> TableCurve:
        self.build()  # empty, over 1024 points, or a non-finite one is refused at load
        return self


type CurveModel = Annotated[LinearCurve | TableCurve, Field(discriminator="type")]


class Curve(Derived):
    """One signal through a calibration curve: `value` is `curve(x)`, none outside its domain."""

    x = Input("x", "Input")

    def __init__(
        self,
        name: str,
        curve: Linear | Table,
        quantity: Quantity,
        label: str | None = None,
        config: CurveConfig | None = None,
    ) -> None:
        super().__init__(name, label)
        self.curve = curve
        self._config = config
        self.bind([
            SignalSpec(name="value", quantity=quantity, access=Access.RP, role=Role.READOUT)
        ])
        self.value = self.signals["value"]

    @property
    def config(self) -> CurveConfig:
        """The config this was built from, or one describing it when built in code."""
        if self._config is not None:
            return self._config
        curve: LinearCurve | TableCurve = (
            LinearCurve(type="linear", scale=self.curve.scale, offset=self.curve.offset)
            if isinstance(self.curve, Linear)
            else TableCurve(type="table", points=list(self.curve.points))
        )
        unit = self.value.unit.symbol
        return CurveConfig(curve=curve, unit=unit or None, quantity=self.value.quantity.name)

    def compute(self, values: Mapping[str, Value]) -> Mapping[Signal, Value]:
        return {self.value: self.evaluate(values["x"])}

    def evaluate(self, x: Value) -> float | NoValue:
        """`curve(x)`; `invalid("out_of_domain")` beyond the curve's domain, never extrapolated."""
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return invalid("not_a_number")
        if not self.curve.in_domain(x):
            return invalid("out_of_domain", "low" if x < self.curve.domain[0] else "high")
        return self.curve(x)

    def raw_of(self, signal: Signal) -> Signal | None:
        """`value` is the engineering value of the signal `x` follows; a number has none."""
        return self.x.signal if signal is self.value else None


class CurveConfig(DriverConfig[Curve], type="curve"):
    """A signal through a calibration curve: one input `x`, one readout `value`."""

    curve: CurveModel = Field(
        description="`{type: linear, scale, offset}` or `{type: table, points: [[x, y], ...]}`."
    )
    unit: str | None = Field(
        default=None, description="The unit symbol of `value` (`NTU`, `pH`, `kPa`); omit for none."
    )
    quantity: str | None = Field(
        default=None, description="What `value` is (`turbidity`); default: the device's name."
    )

    @field_validator("unit")
    @classmethod
    def _known(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                Unit.get(value)
            except UnitNotFoundError as e:
                raise ValueError(str(e)) from None
        return value

    def build(self, name: str, label: str | None = None) -> Curve:
        quantity = (
            Quantity(self.quantity or name, Unitless)
            if self.unit is None
            else Quantity(self.quantity or name, self.unit)
        )
        return Curve(name, self.curve.build(), quantity, label, self)


# endregion

__all__ = ["Curve", "CurveConfig", "CurveModel", "Derived", "LinearCurve", "TableCurve"]
