"""Modbus controllers over a register link: one register per signal.

A [Modbus][flyball.devices.modbus.Modbus] device's tree is its own config:
`registers` maps each signal's name to where it lives and how it converts.
Block reads where addresses are contiguous would be an optimisation; this
driver reads and writes one register at a time.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from flyball.core.config import resolve
from flyball.core.device import Device, DriverConfig
from flyball.core.quantity import Quantity
from flyball.core.signal import Access, Node, Sample, Signal, SignalSpec
from flyball.hardware.links import FakeRegisterLink, RegisterLink, RegisterLinkConfig

Kind = Literal["holding", "input", "coil"]


class ModbusRegister(BaseModel):
    """One line of a `modbus` device's tree: where a value lives, and how it converts.

    `value = raw * scale`. Every register is readable and published; `write`
    also makes it writable. `input` registers are read-only on real hardware,
    so `write` on one is refused here too.
    """

    model_config = ConfigDict(extra="forbid")

    address: int
    kind: Kind = "holding"
    unit: str
    scale: float = 1.0
    write: bool = False

    @model_validator(mode="after")
    def _input_is_read_only(self) -> ModbusRegister:
        if self.kind == "input" and self.write:
            raise ValueError("an input register cannot be written")
        return self

    @property
    def access(self) -> Access:
        return Access.RPW if self.write else Access.RP


class Modbus(Device):
    """Modbus registers as signals: each of `registers` becomes one.

    Args:
        name: The device's name.
        link: What to talk over.
        registers: `{name: ModbusRegister}` -- the tree, and where each signal lives.
        unit_id: The Modbus device address on a shared bus.
    """

    def __init__(
        self,
        name: str,
        link: RegisterLink,
        registers: Mapping[str, ModbusRegister],
        unit_id: int = 1,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.unit_id = unit_id
        self.registers = dict(registers)
        # Device.blocking is a ClassVar; this driver's real bus or fake is only known
        # per instance, at build.
        self.blocking = not isinstance(link, FakeRegisterLink)  # pyright: ignore[reportAttributeAccessIssue]
        self._last_read: dict[Signal, int] = {}
        self.bind([
            SignalSpec(name=key, quantity=Quantity(key, reg.unit), access=reg.access)
            for key, reg in self.registers.items()
        ])

    def _due(self, signal: Signal, time_ns: int) -> bool:
        poll_s = signal.poll_s
        if poll_s is None:
            return True
        last = self._last_read.get(signal)
        return last is None or (time_ns - last) >= poll_s * 1e9

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """One register read per due, publishing signal under `node`: each its own instant."""
        target = node if node is not None else self.root
        for signal in target.walk():
            if Access.P not in signal.access or not self._due(signal, time_ns):
                continue
            register = self.registers[signal.name]
            (word,) = self.link.read_registers(register.address, 1, self.unit_id)
            value = word * register.scale
            self._last_read[signal] = time_ns
            yield Sample(self.root, time_ns, {signal: value})

    def write_signal(self, signal: Signal, value: float) -> None:
        register = self.registers[signal.name]
        self.link.write_registers(register.address, [round(value / register.scale)], self.unit_id)


class ModbusConfig(DriverConfig[Modbus], tag="modbus"):
    """`driver: modbus`. `registers` is the driver's own tree -- see `ModbusRegister`."""

    link: RegisterLinkConfig | str  # type: ignore[valid-type]
    registers: dict[str, ModbusRegister]
    unit_id: int = 1

    def build(self, name: str, label: str | None = None) -> Modbus:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a link before building")
        return Modbus(name, resolve(self.link), self.registers, self.unit_id, label=label)


__all__ = ["Modbus", "ModbusConfig", "ModbusRegister"]
