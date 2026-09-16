"""DS18B20 and its family through the kernel's `w1_therm` driver: `temperature [RP]` in °C."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from flyball.core.config import resolve
from flyball.core.device import Device, DeviceState, DriverConfig
from flyball.core.errors import HardwareError
from flyball.core.quantity import Quantity
from flyball.core.signal import Access, Node, Sample, SignalSpec
from flyball.core.units.si import Celsius

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


@dataclass(frozen=True, slots=True, kw_only=True)
class Ds18b20State(DeviceState):
    temperature: float | None = None


class Ds18b20(Device):
    """One probe by id (`28-0316a279...`), read as `temperature` in °C.

    A read takes ~750 ms at 12-bit resolution, on the kernel's thread; poll no
    faster than once a second.
    """

    TREE = (
        SignalSpec(
            name="temperature",
            quantity=TEMPERATURE,
            access=Access.RP,
            range=(-55.0, 125.0),
            precision=3,
        ),
    )

    def __init__(self, name: str, link: OneWireLink, device: str, label: str | None = None) -> None:
        super().__init__(name, label)
        self.link = link
        self.device = device
        self._temperature: float | None = None

    @property
    def config(self) -> Ds18b20Config:
        return Ds18b20Config(link="", device=self.device)

    @property
    def state(self) -> Ds18b20State:
        return Ds18b20State(temperature=self._temperature)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        self._temperature = parse_w1_slave(self.link.read(self.device))
        yield Sample(self.root, time_ns, {self.signals["temperature"]: self._temperature})


class Ds18b20Config(DriverConfig[Ds18b20], tag="ds18b20"):
    """`driver: ds18b20`: the probe's id under `/sys/bus/w1/devices`."""

    link: OneWireLinkConfig | str  # type: ignore[valid-type]
    device: str

    def build(self, name: str, label: str | None = None) -> Ds18b20:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Ds18b20(name, resolve(self.link), self.device, label=label)


Ds18b20.config_type = Ds18b20Config  # the config is declared after the device it builds


__all__ = ["Ds18b20", "Ds18b20Config", "Ds18b20State", "parse_w1_slave"]
