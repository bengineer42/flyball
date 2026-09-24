"""Register-mapped I2C chips: a table says where each value lives and how to read it.

Most sensors with a datasheet register map need no driver: TMP117, MCP9808,
INA219, LM75, PCF8591. An [I2cTable][flyball_linux.devices.i2c_table.I2cTable]
device's tree is its own config: `registers` maps each signal's name to a
register, `value = raw * scale + offset` after the bytes are assembled in
the order and signedness the table says. A register with `write: true` is
also a demand `[RPW]` -- a DAC's output, a setpoint -- and reads back what
the chip holds, quantised. `init` writes a fixed list of registers once,
when the device is built -- a mode or reset register a signal never needs
to read back.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
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
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    mask: int | None = Field(
        default=None,
        ge=0,
        description="AND with the shifted raw value: a bit-field inside a wider register.",
    )
    sign_bit: int | None = Field(
        default=None,
        ge=0,
        le=63,
        description=(
            "The masked value's own sign bit (0-based): two's-complement at that width,"
            " not the register's. A bit-field register (`mask` or `sign_bit` set) is"
            " read-only here -- there is no safe read-modify-write of the other bits."
        ),
    )
    scale: float = 1.0
    offset: float = 0.0
    unit: str = "1"
    quantity: str | None = Field(
        default=None, description="The quantity's own name, if it differs from the signal's."
    )
    write: bool = Field(default=False, description="Also a demand: a DAC output, a setpoint.")
    role: Literal["setting"] | None = Field(
        default=None,
        description=(
            "`setting`: a writable entry that changes how the instrument behaves (a range,"
            " a frequency, a configuration register), not what controls the process; a"
            " controller cannot drive it. Omitted: a writable entry is a demand."
        ),
    )

    @model_validator(mode="after")
    def _setting_is_writable(self) -> Register:
        if self.role is not None and not self.write:
            raise ValueError("only a writable register can be declared a setting")
        if (self.mask is not None or self.sign_bit is not None) and self.write:
            raise ValueError("a register with a mask or sign_bit is read-only")
        return self

    @property
    def access(self) -> Access:
        return Access.RPW if self.write else Access.RP

    @property
    def signal_role(self) -> Role:
        if not self.write:
            return Role.READOUT
        return Role.SETTING if self.role == "setting" else Role.DEMAND

    def decode(self, data: bytes) -> float:
        if len(data) != self.length:
            raise ValueError(
                f"register 0x{self.address:02x} wants {self.length} bytes, got {len(data)}"
            )
        if self.mask is None and self.sign_bit is None:
            raw = int.from_bytes(data, self.byteorder, signed=self.signed) >> self.shift
        else:
            raw = int.from_bytes(data, self.byteorder, signed=False) >> self.shift
            if self.mask is not None:
                raw &= self.mask
            if self.sign_bit is not None and raw & (1 << self.sign_bit):
                raw -= 1 << (self.sign_bit + 1)
        return raw * self.scale + self.offset

    def encode(self, value: float) -> bytes:
        raw = round((value - self.offset) / self.scale) << self.shift
        return raw.to_bytes(self.length, self.byteorder, signed=self.signed)


class InitWrite(BaseModel):
    """One write sent when the device is built: a config or reset register with no signal."""

    model_config = ConfigDict(extra="forbid")

    address: int = Field(ge=0, le=0xFF, description="The register address on the chip.")
    value: int
    length: int = Field(default=1, ge=1, le=8, description="Bytes.")
    byteorder: Literal["big", "little"] = "big"
    signed: bool = False

    def encode(self) -> bytes:
        return self.value.to_bytes(self.length, self.byteorder, signed=self.signed)


class I2cTable(Readable, Committable):
    """A table of registers at one address: each of `registers` becomes one signal."""

    def __init__(
        self,
        name: str,
        link: I2cLink,
        address: int,
        registers: Mapping[str, Register],
        init: Sequence[InitWrite] = (),
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        if not registers:
            raise ValueError(f"{name}: an i2c_table reads at least one register")
        self.link = link
        self.address = address
        self.registers = dict(registers)
        self.init = list(init)
        # Device.blocking is a ClassVar; this driver's real bus or fake is only known
        # per instance, at build. The link itself says whether it wants the Writer
        # thread -- a real bus always does, a fake only if configured to.
        self.blocking = link.blocking  # pyright: ignore[reportAttributeAccessIssue]
        self._scan = Scan()
        self.bind([
            SignalSpec(
                name=key,
                quantity=Quantity(r.quantity or key, r.unit),
                access=r.access,
                role=r.signal_role,
            )
            for key, r in self.registers.items()
        ])
        self._signals = [self.signals[key] for key in self.registers]
        for w in self.init:
            self.link.write_register(self.address, w.address, w.encode())

    @property
    def config(self) -> I2cTableConfig:
        return I2cTableConfig(
            link="", address=self.address, registers=self.registers, init=self.init
        )

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
        """Write each staged register; push what the chip now holds if it differs, quantised."""
        for signal, value in self.staged.items():
            register = self.registers[signal.name]
            data = register.encode(value)
            self.link.write_register(self.address, register.address, data)
            readback = register.decode(data)
            if readback != value:
                signal.push(readback, time_ns)


class I2cTableConfig(DriverConfig[I2cTable], type="i2c_table"):
    """`driver: i2c_table`. `registers` is the driver's own tree -- see `Register`.

    ```yaml
    board_temp:
      driver: i2c_table
      link: i2c1
      address: 0x48
      init:
        - { address: 0x01, value: 0x60 }   # one-shot mode, per the datasheet
      registers:
        temperature: { address: 0, length: 2, signed: true, scale: 0.0078125, unit: "°C" }
    ```
    """

    link: I2cLinkConfig | str  # type: ignore[valid-type]
    address: int = Field(ge=0x03, le=0x77, description="The chip's bus address.")
    registers: dict[str, Register]
    init: list[InitWrite] = Field(
        default_factory=list,
        description="Writes sent once, in order, when the device is built.",
    )

    def build(self, name: str, label: str | None = None) -> I2cTable:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return I2cTable(
            name, resolve(self.link), self.address, self.registers, self.init, label=label
        )


I2cTable.config_type = I2cTableConfig  # the config is declared after the device it builds


__all__ = ["I2cTable", "I2cTableConfig", "InitWrite", "Register"]
