"""Register-mapped I2C chips: a table says where each value lives and how to read it.

Most sensors with a datasheet register map need no driver: TMP117, MCP9808,
INA219, LM75, PCF8591. An [I2cTable][flyball_linux.devices.i2c_table.I2cTable]
device's tree is its own config: `registers` maps each signal's name to a
register, `value = raw * scale + offset` after the bytes are assembled in
the order and signedness the table says. A register with `write: true` is
also a demand `[RPW]` -- a DAC's output, a setpoint -- and reads back what
the chip holds, quantised.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Literal

from flyball.foundation.config import resolve
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
from flyball.hardware.i2c import I2cLink
from flyball.hardware.scan import Scan
from pydantic import BaseModel, ConfigDict, Field

from flyball_linux.links.i2c import I2cLinkConfig


class Register(BaseModel):
    """Where a value lives and how it converts: one line of an `i2c_table` device's tree."""

    model_config = ConfigDict(extra="forbid")

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
    quantity: str | None = Field(
        default=None, description="The quantity's own name, if it differs from the signal's."
    )
    write: bool = Field(default=False, description="Also a demand: a DAC output, a setpoint.")

    @property
    def access(self) -> Access:
        return Access.RPW if self.write else Access.RP

    @property
    def role(self) -> Role:
        return Role.DEMAND if self.write else Role.READOUT

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


class I2cTable(Readable, Committable):
    """A table of registers at one address: each of `registers` becomes one signal."""

    def __init__(
        self,
        name: str,
        link: I2cLink,
        address: int,
        registers: Mapping[str, Register],
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        if not registers:
            raise ValueError(f"{name}: an i2c_table reads at least one register")
        self.link = link
        self.address = address
        self.registers = dict(registers)
        # Device.blocking is a ClassVar; this driver's real bus or fake is only known
        # per instance, at build. The link itself says whether it wants the Writer
        # thread -- a real bus always does, a fake only if configured to.
        self.blocking = link.blocking  # pyright: ignore[reportAttributeAccessIssue]
        self._scan = Scan()
        self.bind([
            SignalSpec(
                name=key, quantity=Quantity(r.quantity or key, r.unit), access=r.access, role=r.role
            )
            for key, r in self.registers.items()
        ])
        self._signals = [self.signals[key] for key in self.registers]

    @property
    def config(self) -> I2cTableConfig:
        return I2cTableConfig(link="", address=self.address, registers=self.registers)

    def _value(self, signal: Signal) -> float:
        register = self.registers[signal.name]
        data = self.link.read_register(self.address, register.address, register.length)
        return register.decode(data)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """One register read per due signal, all in one sample: one chip, one bus, in turn."""
        due = self._scan.due(self._signals, time_ns, whole=node is not None)
        if due:
            yield Sample(self.root, time_ns, {signal: self._value(signal) for signal in due})

    def commit(self, time_ns: int) -> None:
        """Write each pending register; push what the chip now holds if it differs, quantised."""
        for signal, value in self.pending.items():
            register = self.registers[signal.name]
            data = register.encode(value)
            self.link.write_register(self.address, register.address, data)
            readback = register.decode(data)
            if readback != value:
                signal.push(readback, time_ns)


class I2cTableConfig(DriverConfig[I2cTable], tag="i2c_table"):
    """`driver: i2c_table`. `registers` is the driver's own tree -- see `Register`.

    ```yaml
    board_temp:
      driver: i2c_table
      link: i2c1
      address: 0x48
      registers:
        temperature: { address: 0, length: 2, signed: true, scale: 0.0078125, unit: "°C" }
    ```
    """

    link: I2cLinkConfig | str  # type: ignore[valid-type]
    address: int = Field(ge=0x03, le=0x77, description="The chip's bus address.")
    registers: dict[str, Register]

    def build(self, name: str, label: str | None = None) -> I2cTable:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return I2cTable(name, resolve(self.link), self.address, self.registers, label=label)


I2cTable.config_type = I2cTableConfig  # the config is declared after the device it builds


__all__ = ["I2cTable", "I2cTableConfig", "Register"]
