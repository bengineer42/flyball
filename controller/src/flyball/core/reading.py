"""Channels, the sources that declare them, and the readings taken on them.

A :class:`Source` is one thing that is measured -- a sensor, a fused estimate, a
processor's output -- and it declares its channels once, at construction. A
:class:`Channel` is one measurand from one source; nothing else constructs one,
so there is exactly one object per ``(source, measurand)`` and equality is
identity. A :class:`Reading` is one value on one channel at one instant, in the
channel's unit. Units live on the measurand, never on the channel or the reading.

Applications declare their measurands as module constants --
``HUMIDITY = Measurand("humidity", PercentRH)`` -- or at runtime from config; core
never names one. A measurand is interned on its name, so the same declaration
twice is one object and a second declaration with a different unit is an error.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from pydantic_core import core_schema

from ..units import Unit
from .errors import ConflictError, NotFoundError


class MeasurandConflictError(ConflictError):
    def __init__(self, existing: Measurand, unit: Unit) -> None:
        super().__init__(f"Measurand {existing.name!r} is {existing.unit}, not {unit}")


class MeasurandNotFoundError(NotFoundError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Measurand {name!r} not found")


class SourceExistsError(ConflictError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Source {name!r} already exists")


class SourceNotFoundError(NotFoundError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Source {name!r} not found")


class ChannelNotFoundError(NotFoundError):
    def __init__(self, source: Source, measurand: str) -> None:
        super().__init__(f"Source {source.name!r} has no {measurand!r} channel")


@dataclass(frozen=True, slots=True, eq=False)
class Measurand:
    """What a channel measures, with the unit every reading of it is in.

    Interned on ``name``: constructing one that exists returns the existing
    object, so equality and hashing are identity. ``label`` is for display and
    defaults to the name.
    """

    name: str
    unit: Unit
    label: str

    _registry: ClassVar[dict[str, Measurand]] = {}

    def __new__(cls, name: str, unit: Unit, label: str = "") -> Measurand:
        if (existing := cls._registry.get(name)) is not None:
            if existing.unit != unit:
                raise MeasurandConflictError(existing, unit)
            return existing
        instance = object.__new__(cls)
        object.__setattr__(instance, "name", name)
        object.__setattr__(instance, "unit", unit)
        object.__setattr__(instance, "label", label or name)
        cls._registry[name] = instance
        return instance

    def __init__(self, name: str, unit: Unit, label: str = "") -> None:
        """No-op: ``__new__`` owns construction, so an interned measurand is not overwritten."""

    def __repr__(self) -> str:
        return f"Measurand({self.name!r}, {self.unit})"

    @classmethod
    def get(cls, name: str) -> Measurand:
        try:
            return cls._registry[name]
        except KeyError:
            raise MeasurandNotFoundError(name) from None

    @classmethod
    def all(cls) -> Iterator[Measurand]:
        """Every measurand declared in this process, in declaration order."""
        return iter(cls._registry.values())

    @classmethod
    def forget(cls, name: str) -> None:
        """Drop a measurand from the registry. For tests."""
        cls._registry.pop(name, None)

    @classmethod
    def _from_wire(cls, value: Any) -> Measurand:
        if isinstance(value, cls):
            return value
        try:
            return cls.get(value)
        except NotFoundError as e:
            raise ValueError(str(e)) from e  # pydantic only reports ValueError

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> core_schema.CoreSchema:
        """On the wire a measurand is its name; decode by registry lookup."""
        return core_schema.json_or_python_schema(
            json_schema=core_schema.no_info_after_validator_function(
                cls._from_wire, core_schema.str_schema()
            ),
            python_schema=core_schema.no_info_plain_validator_function(cls._from_wire),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda q: q.name, return_schema=core_schema.str_schema(), when_used="always"
            ),
        )


@dataclass(frozen=True, slots=True, eq=False)
class Channel:
    """One measurand from one source. Constructed only by :class:`Source`.

    ``eq=False`` keeps identity equality and hashing: the source guarantees one
    object per ``(source, measurand)``, so two pointers are the whole comparison.
    """

    source: Source
    measurand: Measurand

    @property
    def name(self) -> str:
        return f"{self.source.name}.{self.measurand.name}"

    @property
    def unit(self) -> Unit:
        return self.measurand.unit

    def __repr__(self) -> str:
        return f"Channel({self.name}, {self.unit})"

    @classmethod
    def _from_wire(cls, value: Any) -> Channel:
        if isinstance(value, cls):
            return value
        try:
            return Source.get(value["source"])._by_name(value["measurand"])
        except NotFoundError as e:
            raise ValueError(str(e)) from e  # pydantic only reports ValueError

    @staticmethod
    def _to_wire(channel: Channel) -> dict[str, str]:
        return {"source": str(channel.source.name), "measurand": channel.measurand.name}

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> core_schema.CoreSchema:
        """Serialise as names; decode by looking the channel up on its source."""
        fields = core_schema.typed_dict_schema({
            "source": core_schema.typed_dict_field(core_schema.str_schema()),
            "measurand": core_schema.typed_dict_field(core_schema.str_schema()),
        })
        return core_schema.json_or_python_schema(
            json_schema=core_schema.no_info_after_validator_function(cls._from_wire, fields),
            python_schema=core_schema.no_info_plain_validator_function(cls._from_wire),
            serialization=core_schema.plain_serializer_function_ser_schema(
                cls._to_wire, return_schema=fields, when_used="always"
            ),
        )

    def latest(self, readings: Sequence[Reading]) -> Reading | None:
        """The last reading on this channel, or None. Assumes time order (see ``Reader``)."""
        return next((r for r in reversed(readings) if r.channel is self), None)


class Source:
    """Something that emits readings. Subclass it, or instantiate it directly.

    Declares its channels once, at construction, and registers itself by name
    so a channel can be found again from its wire form. Names are unique per
    process; two sensors with the same name is an error, not a merge. A
    ``StrEnum`` member is a valid name -- it is a ``str`` -- a plain ``Enum`` is not.
    """

    __slots__ = ("_channels", "name")

    _registry: ClassVar[dict[str, Source]] = {}

    name: str
    _channels: dict[Measurand, Channel]

    def __init__(self, name: str, measurands: Iterable[Measurand] = ()) -> None:
        if name in self._registry:
            raise SourceExistsError(name)
        self.name = name
        self._channels = {}
        self._registry[name] = self
        for measurand in measurands:
            self.declare(measurand)

    def declare(self, measurand: Measurand) -> Channel:
        """Add a channel for ``measurand``, or return the one already declared."""
        if (channel := self._channels.get(measurand)) is None:
            channel = self._channels[measurand] = Channel(self, measurand)
        return channel

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r}, {[q.name for q in self._channels]})"

    @property
    def channels(self) -> Iterator[Channel]:
        return iter(self._channels.values())

    def channel(self, measurand: Measurand) -> Channel:
        try:
            return self._channels[measurand]
        except KeyError:
            raise ChannelNotFoundError(self, measurand.name) from None

    def _by_name(self, name: str) -> Channel:
        return self.channel(Measurand.get(name))

    def __getitem__(self, measurand: Measurand) -> Channel:
        return self.channel(measurand)

    def __contains__(self, measurand: Measurand) -> bool:
        return measurand in self._channels

    @classmethod
    def get(cls, name: str) -> Source:
        try:
            return cls._registry[name]
        except KeyError:
            raise SourceNotFoundError(name) from None

    @classmethod
    def all(cls) -> Iterator[Source]:
        """Every source declared in this process, in declaration order."""
        return iter(cls._registry.values())

    @classmethod
    def forget(cls, name: str) -> None:
        """Drop a source from the registry. For tests and hot-swapped hardware."""
        cls._registry.pop(name, None)


@dataclass(frozen=True, slots=True)
class Point:
    time_ns: int
    value: float


@dataclass(frozen=True, slots=True)
class Reading:
    """One value on one channel at one instant. Identity, then time, then value."""

    sample: Sample
    measurand: Measurand
    time_ns: int
    value: float

    @property
    def seconds(self) -> float:
        return self.time_ns / 1e9

    @property
    def source(self) -> Source:
        return self.sample.source

    @property
    def unit(self) -> Unit:
        return self.measurand.unit

    @property
    def channel(self) -> Channel:
        return self.sample.source[self.measurand]

    @property
    def point(self) -> Point:
        return Point(self.time_ns, self.value)


@dataclass(frozen=True, slots=True)
class Sample:
    """Every measurand of one source at one instant."""

    source: Source
    seq: int
    time_ns: int
    values: Mapping[Measurand, float]

    @property
    def seconds(self) -> float:
        return self.time_ns / 1e9

    def __getitem__(self, measurand: Measurand) -> float:
        return self.values[measurand]

    def __iter__(self) -> Iterator[Reading]:
        return (self.reading(q) for q in self.values)

    def reading(self, measurand: Measurand) -> Reading:
        try:
            value = self.values[measurand]
            return Reading(self, measurand, self.time_ns, value)
        except KeyError:
            raise ChannelNotFoundError(self.source, measurand.name) from None

    @property
    def channels(self) -> set[Channel]:
        return {self.source[measurand] for measurand in self.values}

    def points(self) -> dict[Measurand, Point]:
        return {q: Point(self.time_ns, v) for q, v in self.values.items()}


class Reader(Protocol):
    """Reads one or more sources in one transaction.

    Contract: ``read`` returns its readings in non-decreasing ``time_ns``.
    Equal stamps are allowed -- a bank's samples share one -- but never a step
    backwards. The rig delivers in the order received and never reorders, so
    every observer inherits the guarantee, and ``Channel.latest`` relies on it.
    A reader that assembles history from several sources must sort before
    returning.
    """

    name: str
    sources: Iterable[Source]

    @property
    def channels(self) -> set[Channel]:
        return {ch for src in self.sources for ch in src.channels}

    def read(self, time_ns: int) -> Iterable[Sample]: ...
