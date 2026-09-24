"""Modbus controllers over a register link: one register per signal.

A [Modbus][flyball_modbus.Modbus] device's tree is its own config:
`registers` maps each signal's name to where it lives and how it converts.
Block reads where addresses are contiguous would be an optimisation; this
driver reads and writes one register at a time.
"""

from __future__ import annotations

import struct
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

from ._links import BOOLEAN_KINDS, READ_ONLY_KINDS, Kind, RegisterLinkConfig

Format = Literal["u16", "i16", "u32", "i32", "f32"]
"""How a register's raw word(s) decode to a number. `u16` is today's default: one
unsigned word. The 32-bit formats span two registers, ordered by `word_order`."""

WordOrder = Literal["big", "little"]
"""Which of a 32-bit value's two registers comes first: `big` (the high word at the
lower address -- Alicat-style MFCs and most Modbus float32 instruments) or `little`.
Ignored for the 16-bit formats."""

_WIDE_FORMATS: tuple[Format, ...] = ("u32", "i32", "f32")


def _decode_words(words: list[int], fmt: Format, word_order: WordOrder) -> float:
    if fmt == "u16":
        return float(words[0])
    if fmt == "i16":
        raw = words[0]
        return float(raw - 0x1_0000) if raw >= 0x8000 else float(raw)
    hi, lo = words if word_order == "big" else (words[1], words[0])
    raw = (hi << 16) | lo
    if fmt == "u32":
        return float(raw)
    if fmt == "i32":
        return float(raw - 0x1_0000_0000) if raw >= 0x8000_0000 else float(raw)
    return struct.unpack(">f", raw.to_bytes(4, "big"))[0]  # f32


def _encode_words(value: float, fmt: Format, word_order: WordOrder) -> list[int]:
    if fmt == "f32":
        raw = int.from_bytes(struct.pack(">f", value), "big")
    else:
        bits = 16 if fmt in ("u16", "i16") else 32
        raw = round(value) & ((1 << bits) - 1)
    if fmt in ("u16", "i16"):
        return [raw]
    hi, lo = (raw >> 16) & 0xFFFF, raw & 0xFFFF
    return [hi, lo] if word_order == "big" else [lo, hi]


class ModbusRegister(BaseModel):
    """One line of a `modbus` device's tree: where a value lives, and how it converts.

    `value = decode(words) * scale + offset`. `kind` picks the Modbus table, and so the
    function code: `holding` (FC03/FC16, the default), `input` (FC04, read-only), `coil`
    (FC01/FC05) and `discrete` (FC02, read-only) -- `input` and `discrete` registers are
    read-only on real hardware, so `write` on either is refused here too. Coils and
    discrete inputs are booleans (0/1); `format` and `word_order` do not apply to them.
    Every register is readable and published; `write` also makes it writable.
    """

    model_config = ConfigDict(extra="forbid")

    address: int
    kind: Kind = "holding"
    unit: str
    format: Format = Field(
        default="u16",
        description=(
            "How the word(s) decode: `u16` (default, one unsigned word), `i16`, or the"
            " 32-bit `u32`/`i32`/`f32`, which span two registers ordered by `word_order`."
            " Named `format`, not `type`, so it cannot clash with a device config's own"
            " `type:` discriminator."
        ),
    )
    word_order: WordOrder = Field(
        default="big",
        description="Which register holds the high word of a 32-bit value: `big` (default,"
        " Alicat-style float32 MFCs) or `little`. Ignored for `u16`/`i16`.",
    )
    scale: float = 1.0
    offset: float = 0.0
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
    def _read_only_kind_is_read_only(self) -> ModbusRegister:
        if self.kind in READ_ONLY_KINDS and self.write:
            raise ValueError(f"a {self.kind} register cannot be written")
        if self.role is not None and not self.write:
            raise ValueError("only a writable register can be declared a setting")
        if self.kind in BOOLEAN_KINDS and self.model_fields_set & {"format", "scale", "offset"}:
            raise ValueError(f"a {self.kind} register is a plain 0/1: no format, scale or offset")
        return self

    @property
    def signal_role(self) -> Role:
        if not self.write:
            return Role.READOUT
        return Role.SETTING if self.role == "setting" else Role.DEMAND

    @property
    def access(self) -> Access:
        return Access.RPW if self.write else Access.RP

    @property
    def word_count(self) -> int:
        return 2 if self.kind not in BOOLEAN_KINDS and self.format in _WIDE_FORMATS else 1

    def decode(self, words: list[int]) -> float:
        if self.kind in BOOLEAN_KINDS:
            return float(words[0])
        return _decode_words(words, self.format, self.word_order) * self.scale + self.offset

    def encode(self, value: float) -> list[int]:
        if self.kind in BOOLEAN_KINDS:
            return [1 if value else 0]
        raw = (value - self.offset) / self.scale
        return _encode_words(raw, self.format, self.word_order)


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

        Walks `registers`, not the tree: any `last.*` is in the device's
        tree too, and has no register behind it.
        """
        target = node if node is not None else self.root
        candidates = {
            self.signals[key]: key for key in self.registers if target.contains(self.signals[key])
        }
        for signal in self._scan.due(candidates, time_ns, whole=False):
            register = self.registers[candidates[signal]]
            words = self.link.read_registers(
                register.address, register.word_count, self.unit_id, register.kind
            )
            yield Sample(self.root, time_ns, {signal: register.decode(words)})

    def commit(self, time_ns: int) -> None:
        """Write every staged register once; push back what the quantised word(s) actually set."""
        for signal, value in self.staged.items():
            register = self.registers[signal.name]
            words = register.encode(value)
            self.link.write_registers(register.address, words, self.unit_id, register.kind)
            actual = register.decode(words)
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


__all__ = ["Format", "Modbus", "ModbusConfig", "ModbusRegister", "WordOrder"]
