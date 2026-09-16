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

from .device import Device, DeviceSettings
from .errors import ConflictError, NotFoundError
from .units import Unit
from .utils import Labelled


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
    warn: tuple[float, float] | None
    """The band a value is normal inside (EPICS LOW/HIGH); outside it, a warning."""
    alarm: tuple[float, float] | None
    """The band a value is acceptable inside (EPICS LOLO/HIHI); outside it, an alarm."""

    _registry: ClassVar[dict[str, Measurand]] = {}

    def __new__(
        cls,
        name: str,
        unit: Unit | str,
        label: str = "",
        range: tuple[float, float] | None = None,
        precision: int | None = None,
        *,
        warn: tuple[float, float] | None = None,
        alarm: tuple[float, float] | None = None,
    ) -> Measurand:
        if isinstance(unit, str):  # a symbol, as a file writes it
            unit = Unit.get(unit)
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
        object.__setattr__(instance, "warn", warn)
        object.__setattr__(instance, "alarm", alarm)
        cls._registry[name] = instance
        return instance

    def __init__(
        self,
        name: str,
        unit: Unit | str,
        label: str = "",
        range: tuple[float, float] | None = None,
        precision: int | None = None,
        *,
        warn: tuple[float, float] | None = None,
        alarm: tuple[float, float] | None = None,
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

    __slots__ = ("_channels", "_seq", "label", "name")

    _registry: ClassVar[dict[str, Source]] = {}

    name: str
    label: str | None
    """A display name; None: show `name`."""
    _channels: dict[Measurand, Channel]

    def __init__(
        self, name: str, measurands: Iterable[Measurand] = (), label: str | None = None
    ) -> None:
        if name in self._registry:
            raise SourceExistsError(name)
        self.name = name
        self.label = label
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

    Samples reach the rig through [emit][flyball.core.reading.Reader.emit],
    fed two ways: **polled** -- override
    [read][flyball.core.reading.Reader.read] and the runtime calls it on the
    reader's period; **pushed** -- a callback or thread calls `emit` or
    [push][flyball.core.reading.Reader.push] and the sample reaches the rig
    at once (held until the reader is attached). A reader may do both.

    Contract: samples are emitted in non-decreasing `time_ns`; equal stamps
    are allowed, a step backwards is not. The rig never reorders, so every
    observer inherits this. A reader that both polls and pushes keeps it true
    across the two paths itself. `seq` numbers a source's samples: take it
    from `source.next_seq()`.

    Config, settings, state and commands: see [flyball.core.device][].
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
        """Hand every emitted sample to `deliver` from now on. Anything held goes first."""
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


class Deliver(Labelled):
    """What a streaming reader emits from each block it receives."""

    RAW = "raw", "Every sample"
    MEAN = "mean", "One sample per block: the mean"
    LAST = "last", "One sample per block: the last"
    DECIMATE = "decimate", "Every Nth sample"


@dataclass(frozen=True, slots=True, kw_only=True)
class BlockSettings(DeviceSettings):
    deliver: Deliver = Deliver.RAW
    every: int = 1
    """With ``decimate``: keep one sample in this many."""


class BlockReader(Reader):
    """A reader for a source that streams: a DAQ card, audio, a fast serial dump.

    The hardware samples on its own clock into a buffer and software drains it
    in blocks. A subclass runs that drain -- usually on its own thread -- and
    hands each block to :meth:`emit_block` as rows of values with the block's
    start time and sample period; ``deliver`` decides what reaches the rig:
    every row, one summary per block, or one in N. What the record wants and
    what a loop wants differ, so it is a setting, changeable while running.
    """

    def __init__(
        self,
        name: str,
        source: Source,
        measurands: Sequence[Measurand],
        deliver: Deliver = Deliver.RAW,
        every: int = 1,
    ) -> None:
        super().__init__(name, (source,))
        self.source = source
        self.measurands = tuple(measurands)
        self.deliver = deliver
        self.every = max(1, every)

    @property
    def settings(self) -> BlockSettings:
        return BlockSettings(deliver=self.deliver, every=self.every)

    def set_deliver(self, deliver: Deliver, every: int | None = None) -> BlockSettings:
        self.deliver = deliver
        if every is not None:
            self.every = max(1, every)
        return self.settings

    def emit_block(self, rows: Sequence[Sequence[float]], start_ns: int, period_ns: int) -> None:
        """Rows are samples in time order, each one value per measurand."""
        if not rows:
            return
        stamp = [start_ns + i * period_ns for i in range(len(rows))]
        match self.deliver:
            case Deliver.MEAN:
                n = len(rows)
                mean = [sum(row[j] for row in rows) / n for j in range(len(self.measurands))]
                chosen = [(stamp[-1], mean)]
            case Deliver.LAST:
                chosen = [(stamp[-1], rows[-1])]
            case Deliver.DECIMATE:
                chosen = [
                    (t, r)
                    for i, (t, r) in enumerate(zip(stamp, rows, strict=True))
                    if i % self.every == 0
                ]
            case _:
                chosen = list(zip(stamp, rows, strict=True))
        self.emit(
            Sample(
                self.source,
                self.source.next_seq(),
                t,
                dict(zip(self.measurands, row, strict=True)),
            )
            for t, row in chosen
        )
