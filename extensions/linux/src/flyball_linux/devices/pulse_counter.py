"""A GPIO line as a pulse counter: a hall-effect flow meter (YF-S201-class).

The sensor drives a digital pulse train whose frequency is proportional to
flow rate; `pulses_per_litre` is the sensor's own calibration constant
(450 for a YF-S201) that turns pulses into litres. Two `[RP]` signals: `rate`
(litres/minute, since the previous read) and `count` (the raw, ever-rising
pulse total).

Edge detection and debounce are `flyball_linux.links.gpio.GpiodChip`'s: this
module only drains the count `claim_edge`/`count_edges` give it and turns it
into a rate. **This has never been run against a real flow meter or any
other hardware** -- the debounce period and edge-event draining follow
libgpiod v2's documented `LineSettings`/`wait_edge_events` API (native
per-line debounce, not a polling loop written here), but that API's
behaviour against an actual pulse train is unverified.
"""

from __future__ import annotations

from collections.abc import Iterator

from flyball.foundation.config import resolve
from flyball.foundation.device import Access, DriverConfig, Node, Readable, Role, Sample, SignalSpec
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Litre, Minute, One
from pydantic import Field

from flyball_linux.links.gpio import GpioLink, GpioLinkConfig

LitrePerMinute = Litre / Minute
RATE = Quantity("rate", LitrePerMinute)
COUNT = Quantity("count", One)


class PulseCounter(Readable):
    """One line, claimed for rising-edge detection on construction.

    `pulses_per_litre` is the sensor's calibration constant; `debounce_s` is
    passed straight to the link's native debounce (libgpiod's, on real
    hardware -- see the module docstring for what is and isn't verified).
    """

    def __init__(
        self,
        name: str,
        link: GpioLink,
        line: int,
        pulses_per_litre: float,
        debounce_s: float = 0.0,
        pull_up: bool | None = None,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.line = line
        self.pulses_per_litre = pulses_per_litre
        self.debounce_s = debounce_s
        self.pull_up = pull_up
        self._count = 0.0
        self._last_time_ns: int | None = None
        self.bind((
            SignalSpec(name="rate", quantity=RATE, access=Access.RP, role=Role.OUTPUT),
            SignalSpec(
                name="count", quantity=COUNT, access=Access.RP, role=Role.OUTPUT, precision=0
            ),
        ))
        link.claim_edge(line, debounce_s, pull_up)

    @property
    def config(self) -> PulseCounterConfig:
        return PulseCounterConfig(
            link="",
            line=self.line,
            pulses_per_litre=self.pulses_per_litre,
            debounce_s=self.debounce_s,
            pull_up=self.pull_up,
        )

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """The pulses since the last read, as a rate, plus the running total."""
        pulses = self.link.count_edges(self.line)
        self._count += pulses
        rate = 0.0
        if self._last_time_ns is not None and (elapsed_ns := time_ns - self._last_time_ns) > 0:
            litres = pulses / self.pulses_per_litre
            rate = litres / (elapsed_ns / 1e9) * 60.0
        self._last_time_ns = time_ns
        yield Sample(
            self.root,
            time_ns,
            {self.signals["rate"]: rate, self.signals["count"]: self._count},
        )


class PulseCounterConfig(DriverConfig[PulseCounter], tag="pulse_counter"):
    """`driver: pulse_counter`: `{ link, line, pulses_per_litre }`, or `pin:` from a board profile.

    `pulses_per_litre` is the sensor's datasheet constant (450 for a
    YF-S201); `debounce_s` guards against contact bounce or electrical
    noise on the pulse line.
    """

    link: GpioLinkConfig | str  # type: ignore[valid-type]
    line: int = Field(ge=0)
    pulses_per_litre: float = Field(gt=0)
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
            self.pulses_per_litre,
            self.debounce_s,
            self.pull_up,
            label=label,
        )


PulseCounter.config_type = PulseCounterConfig  # the config is declared after the device it builds


__all__ = ["COUNT", "RATE", "PulseCounter", "PulseCounterConfig"]
