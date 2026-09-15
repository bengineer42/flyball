"""Modbus controllers over a register link: a register per measurand or demand.

A [Register][flyball.devices.modbus.Register] says where a value lives and
how to read it: kind (`u16`, `s16`, `u32`, `s32`, `f32`), scale, offset,
word order. A [ModbusReader][flyball.devices.modbus.ModbusReader] is a table
of them; a [ModbusActuator][flyball.devices.modbus.ModbusActuator] writes
one from the loop's demand.

"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from flyball.core.config import resolve
from flyball.core.device import DeviceConfig, DeviceState
from flyball.core.reading import Measurand, Reader, Sample, Source
from flyball.core.sink import Actuator, ActuatorState
from flyball.core.units.dimension import Unit
from flyball.core.units.si import One
from flyball.hardware.links import RegisterLink, RegisterLinkConfig

Kind = Literal["u16", "s16", "u32", "s32", "f32"]
_WORDS: dict[str, int] = {"u16": 1, "s16": 1, "u32": 2, "s32": 2, "f32": 2}
_FORMAT: dict[str, str] = {"u16": "H", "s16": "h", "u32": "I", "s32": "i", "f32": "f"}


class Register(BaseModel):
    """Where a value lives and how to read it. `value = raw * scale + offset`."""

    address: int
    kind: Kind = "u16"
    scale: float = 1.0
    offset: float = 0.0
    word_order: Literal["big", "little"] = "big"
    unit: str = ""
    label: str = ""
    range: tuple[float, float] | None = None
    precision: int | None = None
    warn: tuple[float, float] | None = None
    alarm: tuple[float, float] | None = None

    @property
    def words(self) -> int:
        return _WORDS[self.kind]

    def decode(self, words: list[int]) -> float:
        if len(words) != self.words:
            raise ValueError(f"register {self.address} wants {self.words} words, got {len(words)}")
        if self.word_order == "little":
            words = list(reversed(words))
        raw = struct.pack(">" + "H" * len(words), *words)
        (value,) = struct.unpack(">" + _FORMAT[self.kind], raw)
        return value * self.scale + self.offset

    def encode(self, value: float) -> list[int]:
        raw = (value - self.offset) / self.scale
        packed = struct.pack(">" + _FORMAT[self.kind], raw if self.kind == "f32" else round(raw))
        words = list(struct.unpack(">" + "H" * self.words, packed))
        return list(reversed(words)) if self.word_order == "little" else words


@dataclass(frozen=True, slots=True, kw_only=True)
class ModbusState(DeviceState):
    values: dict[str, float] = field(default_factory=dict)


class ModbusReader(Reader):
    """Polls a table of registers as one source."""

    def __init__(
        self,
        name: str,
        link: RegisterLink,
        registers: Mapping[str, Register],
        unit_id: int = 1,
        source: str | None = None,
    ) -> None:
        self.link = link
        self.unit_id = unit_id
        self.table = dict(registers)
        self.measurands = {
            key: Measurand(
                key,
                Unit.get(r.unit) if r.unit else One,
                r.label,
                r.range,
                r.precision,
                warn=r.warn,
                alarm=r.alarm,
            )
            for key, r in registers.items()
        }
        self.source = Source(source or name, self.measurands.values())
        super().__init__(name, (self.source,))
        self._values: dict[str, float] = {}

    @property
    def state(self) -> ModbusState:
        return ModbusState(values=dict(self._values))

    def read(self, time_ns: int) -> Iterable[Sample]:
        values = {
            self.measurands[key]: r.decode(
                self.link.read_registers(r.address, r.words, self.unit_id)
            )
            for key, r in self.table.items()
        }
        self._values = {m.name: v for m, v in values.items()}
        return [Sample(self.source, self.source.next_seq(), time_ns, values)]


class ModbusReaderConfig(DeviceConfig[ModbusReader], tag="modbus_reader"):
    name: str
    link: RegisterLinkConfig | str  # type: ignore[valid-type]
    registers: dict[str, Register]
    unit_id: int = 1
    source: str | None = None

    def build(self) -> ModbusReader:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a link before building")
        return ModbusReader(
            self.name, resolve(self.link), self.registers, self.unit_id, self.source
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ModbusActuatorState(ActuatorState):
    written: list[int] = field(default_factory=list)
    """The words last written, for checking against the controller's own display."""


class ModbusActuator(Actuator):
    """Writes one register from the loop's demand."""

    def __init__(self, name: str, link: RegisterLink, register: Register, unit_id: int = 1) -> None:
        super().__init__(name)
        self.link = link
        self.register = register
        self.unit_id = unit_id
        self._demand: float | None = None
        self._written: list[int] = []
        if register.unit:
            self.demand_unit = Unit.get(register.unit)  # type: ignore[misc]

    @property
    def state(self) -> ModbusActuatorState:
        return ModbusActuatorState(demand=self._demand, written=list(self._written))

    def set_demand(self, demand: float) -> float | None:
        self._demand = demand
        self._written = self.register.encode(demand)
        self.link.write_registers(self.register.address, self._written, self.unit_id)
        return self.register.decode(self._written)  # what the controller will hold, quantised


class ModbusActuatorConfig(DeviceConfig[ModbusActuator], tag="modbus_actuator"):
    name: str
    link: RegisterLinkConfig | str  # type: ignore[valid-type]
    output: Register  # `register` would shadow ABC.register on the config
    unit_id: int = 1

    def build(self) -> ModbusActuator:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a link before building")
        return ModbusActuator(self.name, resolve(self.link), self.output, self.unit_id)
