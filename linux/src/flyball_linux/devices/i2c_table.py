"""Register-mapped I2C chips: a table says where each value lives and how to read it.

Most sensors with a datasheet register map need no driver: TMP117, MCP9808,
INA219, LM75, PCF8591. `value = raw * scale + offset` after the bytes are
assembled in the order and signedness the table says.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from flyball.core.config import resolve
from flyball.core.device import DeviceConfig, DeviceState
from flyball.core.reading import Measurand, Reader, Sample, Source
from flyball.core.sink import Actuator, ActuatorState
from flyball.core.units.dimension import Unit
from pydantic import BaseModel, Field

from flyball_linux.links.i2c import I2cLink, I2cLinkConfig


class Register(BaseModel):
    """Where a value lives and how to read it."""

    address: int = Field(ge=0, le=0xFF, description="The register address on the chip.")
    length: int = Field(default=2, ge=1, le=8, description="Bytes.")
    signed: bool = False
    byteorder: Literal["big", "little"] = "big"
    shift: int = Field(
        default=0, ge=0, description="Right-shift the raw value first: 12-bit left-justified ADCs."
    )
    scale: float = 1.0
    offset: float = 0.0
    unit: str = "1"
    label: str = ""
    range: tuple[float, float] | None = None
    precision: int | None = None

    def decode(self, data: bytes) -> float:
        if len(data) != self.length:
            raise ValueError(
                f"register 0x{self.address:02x} wants {self.length} bytes, got {len(data)}"
            )
        raw = int.from_bytes(data, self.byteorder, signed=self.signed) >> self.shift
        return raw * self.scale + self.offset

    def encode(self, value: float) -> bytes:
        raw = round((value - self.offset) / self.scale) << self.shift
        return raw.to_bytes(self.length, self.byteorder, signed=self.signed)


@dataclass(frozen=True, slots=True, kw_only=True)
class I2cTableState(DeviceState):
    values: dict[str, float] = field(default_factory=dict)


class I2cReader(Reader):
    """Polls a table of registers at one address as one source."""

    def __init__(
        self,
        name: str,
        link: I2cLink,
        address: int,
        registers: Mapping[str, Register],
        source: str | None = None,
    ) -> None:
        self.link = link
        self.address = address
        self.table = dict(registers)
        self.measurands = {
            key: Measurand(key, Unit.get(r.unit), r.label, r.range, r.precision)
            for key, r in registers.items()
        }
        self.source = Source(source or name, self.measurands.values())
        super().__init__(name, (self.source,))
        self._values: dict[str, float] = {}

    @property
    def state(self) -> I2cTableState:
        return I2cTableState(values=dict(self._values))

    def read(self, time_ns: int) -> Iterable[Sample]:
        values = {
            self.measurands[key]: r.decode(
                self.link.read_register(self.address, r.address, r.length)
            )
            for key, r in self.table.items()
        }
        self._values = {m.name: v for m, v in values.items()}
        return [Sample(self.source, self.source.next_seq(), time_ns, values)]


class I2cReaderConfig(DeviceConfig[I2cReader], tag="i2c_reader"):
    """A register-mapped chip.

    ```toml
    [readers.device.registers.temperature]
    address = 0
    length = 2
    signed = true
    scale = 0.0078125
    unit = "°C"
    ```
    """

    name: str
    link: I2cLinkConfig | str  # type: ignore[valid-type]
    address: int = Field(ge=0x03, le=0x77, description="The chip's bus address.")
    registers: dict[str, Register]
    source: str | None = None

    def build(self) -> I2cReader:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return I2cReader(self.name, resolve(self.link), self.address, self.registers, self.source)


@dataclass(frozen=True, slots=True, kw_only=True)
class I2cActuatorState(ActuatorState):
    written: bytes = b""


class I2cActuator(Actuator):
    """Writes one register from the loop's demand: a DAC, a setpoint register."""

    def __init__(self, name: str, link: I2cLink, address: int, output: Register) -> None:
        super().__init__(name)
        self.link = link
        self.address = address
        self.output = output
        self._demand: float | None = None
        self._written = b""
        self.demand_unit = Unit.get(output.unit)  # type: ignore[misc]

    @property
    def state(self) -> I2cActuatorState:
        return I2cActuatorState(demand=self._demand, written=self._written)

    def set_demand(self, demand: float) -> float | None:
        self._demand = demand
        self._written = self.output.encode(demand)
        self.link.write_register(self.address, self.output.address, self._written)
        return self.output.decode(self._written)  # what the chip holds, quantised


class I2cActuatorConfig(DeviceConfig[I2cActuator], tag="i2c_actuator"):
    name: str
    link: I2cLinkConfig | str  # type: ignore[valid-type]
    address: int = Field(ge=0x03, le=0x77, description="The chip's bus address.")
    output: Register

    def build(self) -> I2cActuator:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return I2cActuator(self.name, resolve(self.link), self.address, self.output)


__all__ = [
    "I2cActuator",
    "I2cActuatorConfig",
    "I2cReader",
    "I2cReaderConfig",
    "I2cTableState",
    "Register",
]
