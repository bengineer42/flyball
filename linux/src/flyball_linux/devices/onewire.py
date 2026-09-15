"""DS18B20 and its family through the kernel's `w1_therm` driver."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from flyball.core.config import resolve
from flyball.core.device import DeviceConfig, DeviceState
from flyball.core.errors import HardwareError
from flyball.core.reading import Measurand, Reader, Sample, Source
from flyball.core.units.dimension import Unit

from flyball_linux.links.onewire import OneWireLink, OneWireLinkConfig

_TEMPERATURE = re.compile(r"t=(-?\d+)\s*$")


def parse_w1_slave(text: str) -> float:
    """°C from the two-line `w1_slave` text; raises if the CRC line says NO.

    Raises:
        HardwareError: A failed CRC or a malformed reading.
    """
    lines = text.strip().splitlines()
    if len(lines) < 2 or not lines[0].rstrip().endswith("YES"):
        raise HardwareError(f"1-Wire CRC failed: {text!r}")
    match = _TEMPERATURE.search(lines[1])
    if match is None:
        raise HardwareError(f"no temperature in {text!r}")
    return int(match.group(1)) / 1000.0


@dataclass(frozen=True, slots=True, kw_only=True)
class Ds18b20State(DeviceState):
    temperature: float | None = None


class Ds18b20Reader(Reader):
    """One probe by id (`28-0316a279...`), read as `temperature` in °C.

    A read takes ~750 ms at 12-bit resolution, on the kernel's thread; poll no
    faster than once a second.
    """

    def __init__(
        self,
        name: str,
        link: OneWireLink,
        device: str,
        measurand: str = "temperature",
        precision: int = 3,
    ) -> None:
        self.link = link
        self.device = device
        self.measurand = Measurand(
            measurand, Unit.get("°C"), range=(-55.0, 125.0), precision=precision
        )
        self.source = Source(name, (self.measurand,))
        super().__init__(name, (self.source,))
        self._temperature: float | None = None

    @property
    def state(self) -> Ds18b20State:
        return Ds18b20State(temperature=self._temperature)

    def read(self, time_ns: int) -> Iterable[Sample]:
        self._temperature = parse_w1_slave(self.link.read(self.device))
        return [
            Sample(
                self.source, self.source.next_seq(), time_ns, {self.measurand: self._temperature}
            )
        ]


class Ds18b20Config(DeviceConfig[Ds18b20Reader], tag="ds18b20"):
    name: str
    link: OneWireLinkConfig | str  # type: ignore[valid-type]
    device: str
    measurand: str = "temperature"
    precision: int = 3

    def build(self) -> Ds18b20Reader:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Ds18b20Reader(
            self.name, resolve(self.link), self.device, self.measurand, self.precision
        )


__all__ = ["Ds18b20Config", "Ds18b20Reader", "Ds18b20State", "parse_w1_slave"]
