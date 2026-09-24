"""Sensirion SHT40/41/45: temperature and humidity over I2C, one chip or a set of them.

Command byte, a wait, six bytes back: two of temperature, a CRC, two of
humidity, a CRC. No registers, so this is not a table. `sht4x` is one chip
on the device root (`humidity`, `temperature [RP]`, one transaction);
`sht4x_set` is several on one bus, each its own atomic namespace
(`hum_sensors.dry.humidity`), read one transaction each on its own `poll_s`.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from typing import Literal

from flyball.foundation.config import resolve
from flyball.foundation.device import (
    Access,
    DriverConfig,
    Node,
    NodeSpec,
    Readable,
    Readout,
    Sample,
    SignalSpec,
)
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.dimensions import Fraction
from flyball.foundation.quantities.si import Celsius
from flyball.hardware.i2c import I2cLink
from pydantic import BaseModel, ConfigDict, Field

from flyball_chips._links import I2cLinkConfig
from flyball_chips._sensirion import crc8, crc_words

Precision = Literal["high", "medium", "low"]
COMMANDS: dict[str, tuple[int, float]] = {
    "high": (0xFD, 0.0083),
    "medium": (0xF6, 0.0045),
    "low": (0xE0, 0.0016),
}
"""Measure command and the datasheet's maximum conversion time, by precision."""

PercentRH = Fraction.unit("percent relative humidity", "%RH", 0.01, scale=(0.0, 100.0))
HUMIDITY = Quantity("humidity", PercentRH)
TEMPERATURE = Quantity("temperature", Celsius)
SHT4X_ADDRESS = 0x44
"""The default address; the -B variants answer at 0x45."""


def decode(frame: bytes) -> tuple[float, float]:
    """(°C, %RH) from the six-byte reply.

    Raises:
        HardwareError: A CRC that does not match.
    """
    raw_t, raw_h = crc_words(frame, 2)
    temperature = -45.0 + 175.0 * raw_t / 65535.0
    humidity = min(100.0, max(0.0, -6.0 + 125.0 * raw_h / 65535.0))
    return temperature, humidity


def encode(temperature: float, humidity: float) -> bytes:
    """The frame the chip would send for these values; for fakes and tests."""
    raw_t = round((temperature + 45.0) / 175.0 * 65535.0)
    raw_h = round((humidity + 6.0) / 125.0 * 65535.0)
    t = raw_t.to_bytes(2, "big")
    h = raw_h.to_bytes(2, "big")
    return t + bytes([crc8(t)]) + h + bytes([crc8(h)])


class Sht4xSensor:
    """One chip at `address`: command, wait, read, decode, in one `read`."""

    __slots__ = ("address", "link", "precision", "sleep")

    def __init__(
        self, link: I2cLink, address: int, precision: Precision = "high", sleep: bool = True
    ) -> None:
        self.link = link
        self.address = address
        self.precision: Precision = precision
        self.sleep = sleep
        """Whether to wait the conversion time; off in a test against a fake."""

    def read(self) -> tuple[float, float]:
        """(°C, %RH): one I2C transaction."""
        command, wait_s = COMMANDS[self.precision]
        self.link.write(self.address, [command])
        if self.sleep:
            time.sleep(wait_s)
        return decode(self.link.read(self.address, 6))


def _tree() -> tuple[SignalSpec, ...]:
    return (
        SignalSpec(
            name="humidity", quantity=HUMIDITY, access=Access.RP, range=(0.0, 100.0), precision=2
        ),
        SignalSpec(
            name="temperature",
            quantity=TEMPERATURE,
            access=Access.RP,
            range=(-40.0, 125.0),
            precision=2,
        ),
    )


class Sht4x(Readable):
    """One chip on the device root: `humidity`, `temperature [RP]`, one I2C transaction."""

    humidity = Readout("humidity", quantity=HUMIDITY, range=(0.0, 100.0), precision=2)
    temperature = Readout("temperature", quantity=TEMPERATURE, range=(-40.0, 125.0), precision=2)

    def __init__(
        self,
        name: str,
        link: I2cLink,
        address: int = SHT4X_ADDRESS,
        precision: Precision = "high",
        sleep: bool = True,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.sensor = Sht4xSensor(link, address, precision, sleep)

    @property
    def config(self) -> Sht4xConfig:
        return Sht4xConfig(link="", address=self.sensor.address, precision=self.sensor.precision)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        temperature, humidity = self.sensor.read()
        yield self.sample(time_ns, humidity=humidity, temperature=temperature)


class Sht4xConfig(DriverConfig[Sht4x], type="sht4x"):
    """One chip by its I2C address."""

    link: I2cLinkConfig | str  # type: ignore[valid-type]
    address: int = Field(default=SHT4X_ADDRESS, ge=0x03, le=0x77)
    precision: Precision = "high"

    def build(self, name: str, label: str | None = None) -> Sht4x:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Sht4x(name, resolve(self.link), self.address, self.precision, label=label)


Sht4x.config_type = Sht4xConfig  # the config is declared after the device it builds


class SensorEntry(BaseModel):
    """One sensor of an `sht4x_set`: its I2C address."""

    model_config = ConfigDict(extra="forbid")

    address: int = Field(default=SHT4X_ADDRESS, ge=0x03, le=0x77)


class Sht4xSet(Readable):
    """Several chips on one bus, one atomic namespace each, read one transaction each when due."""

    def __init__(
        self,
        name: str,
        link: I2cLink,
        sensors: Mapping[str, int],
        precision: Precision = "high",
        sleep: bool = True,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        if not sensors:
            raise ValueError(f"{name}: an sht4x_set reads at least one sensor")
        self.link = link
        self.bind(
            tuple(
                NodeSpec(name=sensor_name, atomic=True, children=_tree()) for sensor_name in sensors
            )
        )
        self.sensors = {
            sensor_name: Sht4xSensor(link, address, precision, sleep)
            for sensor_name, address in sensors.items()
        }
        self._last_read_ns: dict[str, int] = {}

    @property
    def config(self) -> Sht4xSetConfig:
        (sensor, *_) = self.sensors.values()
        return Sht4xSetConfig(
            link="",
            sensors={name: SensorEntry(address=s.address) for name, s in self.sensors.items()},
            precision=sensor.precision,
        )

    def _due(self, node: Node, time_ns: int) -> bool:
        last = self._last_read_ns.get(node.name)
        if last is None:
            return True
        period_s = node.poll_s
        return period_s is None or (time_ns - last) >= 0.9 * period_s * 1e9

    def _sample(self, node: Node, time_ns: int) -> Sample:
        temperature, humidity = self.sensors[node.name].read()
        self._last_read_ns[node.name] = time_ns
        return Sample(
            node,
            time_ns,
            {node.signals["humidity"]: humidity, node.signals["temperature"]: temperature},
        )

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """One sample per due namespace, each its own I2C transaction.

        `node` names one namespace: always read, `fresh` or not. `None` (or
        the root, the periodic poll): only the namespaces due on their own
        `poll_s`.
        """
        if node is not None and node is not self.root:
            yield self._sample(node, time_ns)
            return
        for child in self.root.children.values():
            if self._due(child, time_ns):
                yield self._sample(child, time_ns)


class Sht4xSetConfig(DriverConfig[Sht4xSet], type="sht4x_set"):
    """Several chips on one bus, one namespace per sensor: `sensors: {dry: {address: 0x45}}`."""

    link: I2cLinkConfig | str  # type: ignore[valid-type]
    sensors: dict[str, SensorEntry]
    precision: Precision = "high"

    def build(self, name: str, label: str | None = None) -> Sht4xSet:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return Sht4xSet(
            name,
            resolve(self.link),
            {n: s.address for n, s in self.sensors.items()},
            self.precision,
            label=label,
        )


Sht4xSet.config_type = Sht4xSetConfig


__all__ = [
    "COMMANDS",
    "HUMIDITY",
    "SHT4X_ADDRESS",
    "TEMPERATURE",
    "PercentRH",
    "Precision",
    "SensorEntry",
    "Sht4x",
    "Sht4xConfig",
    "Sht4xSensor",
    "Sht4xSet",
    "Sht4xSetConfig",
    "crc8",
    "decode",
    "encode",
]
