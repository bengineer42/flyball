"""Modbus controllers over a register link: one register per signal.

A [Modbus][flyball_modbus.Modbus] device's tree is its own config:
`registers` maps each signal's name to where it lives and how it converts.
Block reads where addresses are contiguous would be an optimisation; this
driver reads and writes one register at a time.
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
    SignalSpec,
)
from flyball.foundation.quantities import Quantity
from flyball.hardware.links import RegisterLink
from flyball.hardware.scan import Scan
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ._links import RegisterLinkConfig

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
    role: Literal["setting"] | None = Field(
        default=None,
        description=(
            "`setting`: a writable entry that changes how the instrument behaves (a range,"
            " a frequency, a configuration register), not what controls the process; a"
            " controller cannot drive it. Omitted: a writable entry is a demand."
        ),
    )

    @model_validator(mode="after")
    def _input_is_read_only(self) -> ModbusRegister:
        if self.kind == "input" and self.write:
            raise ValueError("an input register cannot be written")
        if self.role is not None and not self.write:
            raise ValueError("only a writable register can be declared a setting")
        return self

    @property
    def signal_role(self) -> Role:
        if not self.write:
            return Role.READOUT
        return Role.SETTING if self.role == "setting" else Role.DEMAND

    @property
    def access(self) -> Access:
        return Access.RPW if self.write else Access.RP


class Modbus(Readable, Committable):
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
        # per instance, at build. The link itself says whether it wants the Writer
        # thread -- a real bus always does, a fake only if configured to.
        self.blocking = link.blocking  # pyright: ignore[reportAttributeAccessIssue]
        self._scan = Scan()
        self.bind([
            SignalSpec(
                name=key, quantity=Quantity(key, reg.unit), access=reg.access, role=reg.signal_role
            )
            for key, reg in self.registers.items()
        ])

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """One register read per due, publishing signal under `node`: each its own instant.

        Walks `registers`, not the tree: `conditions` and any `last.*` are in
        every device's tree now, and neither has a register behind it.
        """
        target = node if node is not None else self.root
        candidates = {
            self.signals[key]: key for key in self.registers if target.contains(self.signals[key])
        }
        for signal in self._scan.due(candidates, time_ns, whole=False):
            register = self.registers[candidates[signal]]
            (word,) = self.link.read_registers(register.address, 1, self.unit_id)
            value = word * register.scale
            yield Sample(self.root, time_ns, {signal: value})

    def commit(self, time_ns: int) -> None:
        """Write every staged register once; push back what the quantised word actually set."""
        for signal, value in self.staged.items():
            register = self.registers[signal.name]
            word = round(value / register.scale)
            self.link.write_registers(register.address, [word], self.unit_id)
            actual = word * register.scale
            if actual != value:
                signal.push(actual, time_ns)


class ModbusConfig(DriverConfig[Modbus], type="modbus"):
    """`driver: modbus`. `registers` is the driver's own tree -- see `ModbusRegister`."""

    link: RegisterLinkConfig | str  # type: ignore[valid-type]
    registers: dict[str, ModbusRegister]
    unit_id: int = 1

    def build(self, name: str, label: str | None = None) -> Modbus:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a link before building")
        return Modbus(name, resolve(self.link), self.registers, self.unit_id, label=label)


__all__ = ["Modbus", "ModbusConfig", "ModbusRegister"]
