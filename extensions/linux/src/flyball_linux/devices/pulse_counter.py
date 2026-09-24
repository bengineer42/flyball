"""A GPIO line as a pulse counter: a hall-effect flow meter, a tachometer.

The sensor drives a digital pulse train whose frequency is proportional to
whatever it measures -- flow, rotation, throughput. `per_pulse` is the
sensor's own calibration constant, the amount one pulse represents; `unit`
says what `rate` (and `per_pulse`) are in. Two `[RP]` signals: `rate` (since
the previous read) and `count` (the raw, ever-rising pulse total, always
plain pulses regardless of `unit`).

`unit` is either a plain frequency (`Hz`, `rpm`, ...: a dimensionless count
per time, so `per_pulse` is itself dimensionless -- a whole pulse, or a
fraction of a revolution for a multi-pulse-per-turn encoder) or written
`amount/time` (`L/min`, `mL/s`: `per_pulse` is then in the amount's own
unit -- litres, millilitres). Either way the division by elapsed time is by
whichever time unit the chosen `unit` names -- a minute for `rpm`, a second
for `Hz` or `mL/s` -- so a flow meter reads naturally in litres/minute and a
tachometer in rpm from the same driver, with no unit conversion hidden in
the frequency-vs-rate boundary.

Edge detection and debounce are `flyball_linux.links.gpio.GpiodChip`'s: this
module only drains the count `claim_edge`/`count_edges` give it and turns it
into a rate. **This has never been run against a real flow meter, tachometer
or any other hardware** -- the debounce period and edge-event draining follow
libgpiod v2's documented `LineSettings`/`wait_edge_events` API (native
per-line debounce, not a polling loop written here), but that API's
behaviour against an actual pulse train is unverified.
"""

from __future__ import annotations

from collections.abc import Iterator

from flyball.foundation.config import resolve
from flyball.foundation.device import Access, DriverConfig, Node, Readable, Role, Sample, SignalSpec
from flyball.foundation.quantities import Quantity, Unit
from flyball.foundation.quantities.dimensions import Frequency, Time
from flyball.foundation.quantities.si import One
from pydantic import Field

from flyball_linux.links.gpio import GpioLink, GpioLinkConfig

COUNT = Quantity("count", One)


def _rate(pulses: float, per_pulse: float, elapsed_ns: int, unit: Unit) -> float:
    """`pulses * per_pulse` over `elapsed_ns`, expressed in `unit`.

    Raises:
        ValueError: `unit` is neither a plain frequency nor written `amount/time`.
    """
    elapsed_s = elapsed_ns / 1e9
    if unit.dimension == Frequency:
        # A frequency's numerator is dimensionless: `per_pulse` is already in the
        # unit's own coherent base (Hz), so `unit.factor` alone converts it.
        return pulses * per_pulse / elapsed_s / unit.factor
    if "/" not in unit.symbol:
        raise ValueError(
            f"unit {unit.symbol!r} is not a plain frequency (Hz, rpm, ...): "
            "write it as amount/time, e.g. L/min or mL/s"
        )
    _, _, time_symbol = unit.symbol.rpartition("/")
    time_unit = Unit.get(time_symbol)
    if time_unit.dimension != Time:
        raise ValueError(f"{time_symbol!r} in {unit.symbol!r} is not a time unit")
    elapsed_in_unit_time = elapsed_s / time_unit.factor
    return pulses * per_pulse / elapsed_in_unit_time


class PulseCounter(Readable):
    """One line, claimed for rising-edge detection on construction.

    `per_pulse` is the sensor's calibration constant, in `unit`'s own terms
    (see the module docstring); `debounce_s` is passed straight to the
    link's native debounce (libgpiod's, on real hardware -- see the module
    docstring for what is and isn't verified).
    """

    def __init__(
        self,
        name: str,
        link: GpioLink,
        line: int,
        unit: str,
        per_pulse: float,
        debounce_s: float = 0.0,
        pull_up: bool | None = None,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.line = line
        self.unit = unit
        self._unit = Unit.get(unit)
        self.per_pulse = per_pulse
        self.debounce_s = debounce_s
        self.pull_up = pull_up
        self._count = 0.0
        self._last_time_ns: int | None = None
        self.bind((
            SignalSpec(
                name="rate",
                quantity=Quantity("rate", self._unit),
                access=Access.RP,
                role=Role.READOUT,
            ),
            SignalSpec(
                name="count", quantity=COUNT, access=Access.RP, role=Role.READOUT, precision=0
            ),
        ))
        link.claim_edge(line, debounce_s, pull_up)

    @property
    def config(self) -> PulseCounterConfig:
        return PulseCounterConfig(
            link="",
            line=self.line,
            unit=self.unit,
            per_pulse=self.per_pulse,
            debounce_s=self.debounce_s,
            pull_up=self.pull_up,
        )

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """The pulses since the last read, as a rate, plus the running total."""
        pulses = self.link.count_edges(self.line)
        self._count += pulses
        rate = 0.0
        if self._last_time_ns is not None and (elapsed_ns := time_ns - self._last_time_ns) > 0:
            rate = _rate(pulses, self.per_pulse, elapsed_ns, self._unit)
        self._last_time_ns = time_ns
        yield Sample(
            self.root,
            time_ns,
            {self.signals["rate"]: rate, self.signals["count"]: self._count},
        )


class PulseCounterConfig(DriverConfig[PulseCounter], type="pulse_counter"):
    """`driver: pulse_counter`: `{ link, line, unit, per_pulse }`, or `pin:` from a board profile.

    `per_pulse` is the sensor's datasheet constant, the amount one pulse
    represents in `unit`'s own terms: `1/450` for a YF-S201 (`unit: L/min`,
    450 pulses/litre), or `0.5` for a two-pulse-per-revolution tachometer
    (`unit: rpm`). `debounce_s` guards against contact bounce or electrical
    noise on the pulse line.
    """

    link: GpioLinkConfig | str  # type: ignore[valid-type]
    line: int = Field(ge=0)
    unit: str = Field(description="`rate`'s unit: a plain frequency (Hz, rpm) or amount/time.")
    per_pulse: float = Field(gt=0, description="The amount one pulse represents, in `unit`.")
    debounce_s: float = Field(default=0.0, ge=0)
    pull_up: bool | None = Field(
        default=None, description="The line's bias: true pulls up, false down, omitted as-is."
    )

    def build(self, name: str, label: str | None = None) -> PulseCounter:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a chip before building")
        return PulseCounter(
            name,
            resolve(self.link),
            self.line,
            self.unit,
            self.per_pulse,
            self.debounce_s,
            self.pull_up,
            label=label,
        )


PulseCounter.config_type = PulseCounterConfig  # the config is declared after the device it builds


__all__ = ["COUNT", "PulseCounter", "PulseCounterConfig"]
