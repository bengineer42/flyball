"""DS18B20 and its family through the kernel's `w1_therm` driver: `temperature [RP]` in °C."""

from __future__ import annotations

import re
from collections.abc import Iterator

from flyball.foundation.config import resolve
from flyball.foundation.device import DriverConfig, Node, Readable, Readout, Sample
from flyball.foundation.errors import HardwareError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius

from flyball_linux.links.onewire import OneWireLink, OneWireLinkConfig

_TEMPERATURE = re.compile(r"t=(-?\d+)\s*$")

TEMPERATURE = Quantity("temperature", Celsius)


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


class Ds18b20(Readable):
    """One probe by id (`28-0316a279...`), read as `temperature` in °C.

    A read takes ~750 ms at 12-bit resolution, on the kernel's thread; poll no
    faster than once a second.
    """

    temperature = Readout("temperature", quantity=TEMPERATURE, range=(-55.0, 125.0), precision=3)

    def __init__(self, name: str, link: OneWireLink, device: str, label: str | None = None) -> None:
        super().__init__(name, label)
        self.link = link
        self.device = device

    @property
    def config(self) -> Ds18b20Config:
        return Ds18b20Config(link="", device=self.device)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        temperature = parse_w1_slave(self.link.read(self.device))
        yield self.sample(time_ns, temperature=temperature)


class Ds18b20Config(DriverConfig[Ds18b20], type="ds18b20"):
    """`driver: ds18b20`: the probe's id under `/sys/bus/w1/devices`."""

    link: OneWireLinkConfig | str  # type: ignore[valid-type]
    device: str

    def build(self, name: str, label: str | None = None) -> Ds18b20:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Ds18b20(name, resolve(self.link), self.device, label=label)


Ds18b20.config_type = Ds18b20Config  # the config is declared after the device it builds


__all__ = ["Ds18b20", "Ds18b20Config", "parse_w1_slave"]
