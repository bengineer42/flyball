from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, Protocol

from pydantic_core import core_schema


@dataclass(frozen=True, slots=True)
class Channel:
    source: Source
    quantity: Quantity
    units: Unit

    _channels: ClassVar[dict[tuple[str | Enum | Source, str | Enum], Channel]] = {}

    def __new__(
        cls, source: str | Enum | Source, quantity: str | Enum, units: str | None = None
    ) -> Channel:
        key = (source, quantity)
        if (existing := cls._channels.get(key)) is not None:
            if units is None or units == existing.units:
                return existing
            if existing.units is None:
                object.__setattr__(existing, "units", units)
                return existing
            raise ValueError(f"{source} {quantity} is already {existing.units!r}, not {units!r}")
        instance = object.__new__(cls)
        object.__setattr__(instance, "source", source)
        object.__setattr__(instance, "quantity", quantity)
        object.__setattr__(instance, "units", units)
        cls._channels[key] = instance
        return instance

    def __init__(self, source: str | Enum, quantity: str | Enum, units: str | None = None) -> None:
        """No-op: ``__new__`` owns construction, so a cached channel is not overwritten.

        The parameters mirror ``__new__`` and are unused on purpose -- the
        signature has to match for the call to type-check.
        """

    @classmethod
    def _from_wire(cls, value: Any) -> Channel:
        if isinstance(value, cls):
            return value
        return cls(value["source"], value["quantity"], value.get("units"))

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> core_schema.CoreSchema:
        """Route pydantic through ``__new__`` so a decoded channel is the interned one."""
        fields = core_schema.typed_dict_schema({
            "source": core_schema.typed_dict_field(core_schema.str_schema()),
            "quantity": core_schema.typed_dict_field(core_schema.str_schema()),
            "units": core_schema.typed_dict_field(
                core_schema.nullable_schema(core_schema.str_schema()), required=False
            ),
        })
        return core_schema.json_or_python_schema(
            json_schema=core_schema.no_info_after_validator_function(cls._from_wire, fields),
            python_schema=core_schema.no_info_plain_validator_function(cls._from_wire),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda c: {"source": c.source, "quantity": c.quantity, "units": c.units},
                return_schema=fields,
                when_used="always",
            ),
        )


@dataclass(frozen=True, slots=True)
class Reading:
    time_ns: int
    value: float
    channel: Channel

    @property
    def seconds(self) -> float:
        return self.time_ns / 1e9

    @property
    def source(self) -> str | Enum | Source:
        return self.channel.source

    @property
    def quantity(self) -> str | Enum:
        return self.channel.quantity

    @property
    def units(self) -> str | None:
        return self.channel.units


class Source(Protocol):
    name: str | Enum
    channels: dict[Quantity, Channel]

    def get_channel(self, quantity: str | Enum) -> Channel | None:
        return self.channels.get(quantity)

    def __getitem__(self, quantity: str | Enum) -> Channel:
        return self.channels[quantity]

    def create_channel(self, quantity: str | Enum, units: str | None = None) -> Channel:
        channel = Channel(self.name, quantity, units)
        self.channels[quantity] = channel
        return channel


class Quantity(Protocol):
    name: str | Enum
    dimension: str | Enum
    unit: Unit | None
    units: frozenset[Unit]


class Unit(Protocol):
    _quantity: Quantity
    name: str
    _conversion_to_base: float | None
