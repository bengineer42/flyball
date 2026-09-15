"""Channels, the sources that declare them, and the readings taken on them.

A [Source][flyball.core.reading.Source] is one thing measured -- a sensor, a
fused estimate -- and declares its channels once, at construction. A
[Channel][flyball.core.reading.Channel] is one measurand from one source; only
the source constructs one, so equality is identity. A
[Reading][flyball.core.reading.Reading] is one value on one channel at one
instant. Units live on the measurand only.

Applications declare measurands (`HUMIDITY = Measurand("humidity", PercentRH)`);
core names none. A measurand is interned on its name: redeclaring it returns
the same object, redeclaring with another unit is an error.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic_core import core_schema

from .device import Device
from .errors import ConflictError, NotFoundError
from .units import Unit


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
    """What a channel measures, and its unit.

    Interned on `name`, so equality and hashing are identity. `label` is for
    display and defaults to the name.
    """

    name: str
    unit: Unit
    label: str
    range: tuple[float, float] | None
    """The values a reading can plausibly take, for a gauge or an axis; None if unbounded."""
    precision: int | None
    """Decimal places worth showing; None if the reader has not said."""

    _registry: ClassVar[dict[str, Measurand]] = {}

    def __new__(
        cls,
        name: str,
        unit: Unit,
        label: str = "",
        range: tuple[float, float] | None = None,
        precision: int | None = None,
    ) -> Measurand:
        if (existing := cls._registry.get(name)) is not None:
            if existing.unit != unit:
                raise MeasurandConflictError(existing, unit)
            return existing
        instance = object.__new__(cls)
        object.__setattr__(instance, "name", name)
        object.__setattr__(instance, "unit", unit)
        object.__setattr__(instance, "label", label or name)
        object.__setattr__(instance, "range", range)
        object.__setattr__(instance, "precision", precision)
        cls._registry[name] = instance
        return instance

    def __init__(
        self,
        name: str,
        unit: Unit,
        label: str = "",
        range: tuple[float, float] | None = None,
        precision: int | None = None,
    ) -> None:
        """No-op: `__new__` owns construction, so an interned measurand is not overwritten."""

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
    """One measurand from one source. Constructed only by [Source][flyball.core.reading.Source].

    One object per `(source, measurand)`, so equality is identity.
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
        """The last reading on this channel, or None. Assumes time order (see `Reader`)."""
        return next((r for r in reversed(readings) if r.channel is self), None)


class Source:
    """Something that emits readings. Subclass it, or instantiate it directly.

    Declares its channels at construction and registers itself by name, so a
    channel can be found from its wire form. Names are unique per process; a
    duplicate is an error, not a merge.
    """

    __slots__ = ("_channels", "_seq", "name")

    _registry: ClassVar[dict[str, Source]] = {}

    name: str
    _channels: dict[Measurand, Channel]

    def __init__(self, name: str, measurands: Iterable[Measurand] = ()) -> None:
        if name in self._registry:
            raise SourceExistsError(name)
        self.name = name
        self._channels = {}
        self._seq = 0
        self._registry[name] = self
        for measurand in measurands:
            self.declare(measurand)

    def next_seq(self) -> int:
        """The next sample number for this source. Every sample of a source is one series."""
        self._seq += 1
        return self._seq

    def declare(self, measurand: Measurand) -> Channel:
        """Add a channel for `measurand`, or return the one already declared."""
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


class Reader(Device):
    """A device that delivers samples for one or more sources.

    Samples reach the rig by one path, :meth:`emit`, fed two ways:

    - **polled** -- override :meth:`read`; the runtime calls it on the
      reader's period and emits what it returns.
    - **pushed** -- whatever produces the values (a callback, another thread,
      a subscription) calls :meth:`emit` or :meth:`push` itself, and they
      reach the rig at once. Before the reader is attached to a rig they are
      held and handed over by the default :meth:`read`.

    A reader may do both. Contract: samples are emitted in non-decreasing
    ``time_ns`` -- equal stamps are allowed, a step backwards is not -- and
    the rig never reorders, so every observer inherits it. A reader that
    both polls and pushes must keep that true across the two paths itself; a
    pushed sample stamped "now" always does. ``seq`` numbers a *source's*
    samples: take it from ``source.next_seq()``.

    Config, settings, state and commands: see :mod:`flyball.core.device`.
    """

    sources: Iterable[Source]

    def __init__(self, name: str, sources: Iterable[Source] = ()) -> None:
        super().__init__(name)
        self.sources = tuple(sources)
        self._pending: deque[Sample] = deque()
        self._deliver: Callable[[Sequence[Sample]], None] | None = None

    @property
    def channels(self) -> set[Channel]:
        return {ch for src in self.sources for ch in src.channels}

    # region Delivery

    def attach(self, deliver: Callable[[Sequence[Sample]], None]) -> None:
        """Hand every emitted sample to ``deliver`` from now on -- the rig, in practice.

        Whatever was emitted while unattached goes first, so nothing is lost
        to the order things were wired up in.
        """
        self._deliver = deliver
        if self._pending:
            held = list(self._pending)
            self._pending.clear()
            deliver(held)

    def detach(self) -> None:
        self._deliver = None

    def emit(self, samples: Iterable[Sample]) -> None:
        """Deliver samples now, or hold them until attached. Safe from any thread."""
        samples = list(samples)
        if not samples:
            return
        if self._deliver is not None:
            self._deliver(samples)
        else:
            self._pending.extend(samples)

    def push(self, source: Source, values: Mapping[Measurand, float], time_ns: int) -> None:
        """Emit one sample; the common case for a callback-driven source."""
        self.emit((Sample(source, source.next_seq(), time_ns, dict(values)),))

    # endregion

    def read(self, time_ns: int) -> Iterable[Sample]:
        """Polled readers override this. Default: whatever was emitted while unattached."""
        samples = list(self._pending)
        self._pending.clear()
        return samples
