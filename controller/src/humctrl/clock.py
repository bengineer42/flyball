from __future__ import annotations

import time
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from typing import Any, Self, TypeGuard, overload

from pydantic_core import core_schema

from humctrl.typing import Positive

type Numeric = float | int

type Tm = Numeric | TimeBase

type Td = Numeric | Duration


def normalise_parts(seconds: int, nanoseconds: int) -> tuple[int, int]:
    return divmod(seconds * 1_000_000_000 + nanoseconds, 1_000_000_000)


def is_numeric(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def to_nanoseconds(value: Tm) -> int:
    if isinstance(value, TimeBase):
        return value.nanoseconds
    if is_numeric(value):
        return round(value * 1e9)
    raise TypeError(f"Cannot convert {value!r} to nanoseconds")


def _duration_ns(value: Td) -> int | None:
    if isinstance(value, Duration):
        return value.nanoseconds
    if is_numeric(value):
        return round(value * 1e9)
    return None


class TimeBase:
    _nanoseconds: int = 0

    def __init__(self, seconds: Numeric | None = 0, nanoseconds: int | None = 0) -> None:
        self._nanoseconds = round((seconds or 0) * 1_000_000_000) + (nanoseconds or 0)

    @classmethod
    def from_nanoseconds(cls, nanoseconds: int) -> Self:
        time = cls.__new__(cls)
        time._nanoseconds = nanoseconds
        return time

    @classmethod
    def from_seconds(cls, seconds: float | int) -> Self:
        return cls.from_nanoseconds(round(seconds * 1e9))

    @classmethod
    def from_parts(cls, seconds: int, nanoseconds: int) -> Self:
        time = cls.__new__(cls)
        time.set_parts(seconds, nanoseconds)
        return time

    @classmethod
    def _from_wire(cls, value: Any) -> Self:
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls.from_parts(int(value["seconds"]), int(value["nanoseconds"]))
        return cls.from_nanoseconds(round(float(value) * 1e9))  # lenient: a bare number too

    @classmethod
    def _to_wire(cls, value: TimeBase) -> dict[str, int]:
        seconds, nanoseconds = value.parts
        return {"seconds": seconds, "nanoseconds": nanoseconds}

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> core_schema.CoreSchema:
        parts = core_schema.typed_dict_schema(
            {
                "seconds": core_schema.typed_dict_field(core_schema.int_schema()),
                "nanoseconds": core_schema.typed_dict_field(core_schema.int_schema()),
            }
        )
        return core_schema.json_or_python_schema(
            json_schema=core_schema.no_info_after_validator_function(
                lambda d: cls.from_parts(d["seconds"], d["nanoseconds"]), parts
            ),
            python_schema=core_schema.no_info_plain_validator_function(cls._from_wire),
            serialization=core_schema.plain_serializer_function_ser_schema(
                cls._to_wire, return_schema=parts, when_used="always"
            ),
        )

    @property
    def seconds(self) -> float:
        return self._nanoseconds / 1e9

    @property
    def nanoseconds(self) -> int:
        return self._nanoseconds

    @property
    def parts(self) -> tuple[int, int]:
        return divmod(self._nanoseconds, 1_000_000_000)

    def set_nanoseconds(self, nanoseconds: int) -> None:
        self._nanoseconds = nanoseconds

    def set_seconds(self, seconds: float) -> None:
        self._nanoseconds = round(seconds * 1e9)

    def set_parts(self, seconds: int, nanoseconds: int) -> None:
        self._nanoseconds = seconds * 1_000_000_000 + nanoseconds

    def _is_compatible(self, other: object) -> TypeGuard[Tm]:
        return isinstance(other, (type(self), int, float)) and not isinstance(other, bool)

    def __eq__(self, other: object) -> bool:
        if not self._is_compatible(other):
            return NotImplemented
        return self._nanoseconds == to_nanoseconds(other)

    def __hash__(self) -> int:
        return hash(self._nanoseconds)

    def __lt__(self, other: Tm) -> bool:
        if not self._is_compatible(other):
            return NotImplemented
        return self._nanoseconds < to_nanoseconds(other)

    def __le__(self, other: Tm) -> bool:
        if not self._is_compatible(other):
            return NotImplemented
        return self._nanoseconds <= to_nanoseconds(other)

    def __gt__(self, other: Tm) -> bool:
        if not self._is_compatible(other):
            return NotImplemented
        return self._nanoseconds > to_nanoseconds(other)

    def __ge__(self, other: Tm) -> bool:
        if not self._is_compatible(other):
            return NotImplemented
        return self._nanoseconds >= to_nanoseconds(other)

    def __float__(self) -> float:
        return self._nanoseconds / 1e9

    def __copy__(self) -> Self:
        return type(self).from_nanoseconds(self._nanoseconds)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._nanoseconds})"

    def __str__(self) -> str:
        return f"{self.seconds:}s"


class Time(TimeBase):
    def __add__(self, other: Td) -> Self:
        nanoseconds = _duration_ns(other)
        if nanoseconds is None:
            return NotImplemented
        return type(self).from_nanoseconds(self._nanoseconds + nanoseconds)

    __radd__ = __add__

    @overload
    def __sub__(self, other: Td) -> Self: ...
    @overload
    def __sub__(self, other: Time) -> Duration: ...
    def __sub__(self, other: Td | Time) -> Self | Duration:
        if isinstance(other, Time):
            return Duration.from_nanoseconds(self._nanoseconds - other._nanoseconds)
        nanoseconds = _duration_ns(other)
        if nanoseconds is None:
            return NotImplemented
        return type(self).from_nanoseconds(self._nanoseconds - nanoseconds)


class Duration(TimeBase):
    def __add__(self, other: Td) -> Self:
        nanoseconds = _duration_ns(other)
        if nanoseconds is None:
            return NotImplemented
        return type(self).from_nanoseconds(self._nanoseconds + nanoseconds)

    __radd__ = __add__

    def __sub__(self, other: Td) -> Self:
        nanoseconds = _duration_ns(other)
        if nanoseconds is None:
            return NotImplemented
        return type(self).from_nanoseconds(self._nanoseconds - nanoseconds)

    def __rsub__(self, other: Numeric) -> Self:
        nanoseconds = _duration_ns(other)
        if nanoseconds is None:
            return NotImplemented
        return type(self).from_nanoseconds(nanoseconds - self._nanoseconds)

    def __mul__(self, other: float | int) -> Self:
        return type(self).from_nanoseconds(round(self._nanoseconds * other))

    __rmul__ = __mul__

    @overload
    def __truediv__(self, other: Duration) -> float: ...
    @overload
    def __truediv__(self, other: float | int) -> Self: ...

    def __truediv__(self, other: Td) -> float | Self:
        if isinstance(other, Duration):
            return self._nanoseconds / other._nanoseconds
        if is_numeric(other):
            return type(self).from_nanoseconds(round(self._nanoseconds / other))
        return NotImplemented

    @overload
    def __floordiv__(self, other: Duration) -> int: ...
    @overload
    def __floordiv__(self, other: float | int) -> Self: ...
    def __floordiv__(self, other: Td) -> int | Self:
        if isinstance(other, Duration):
            return self._nanoseconds // other._nanoseconds
        if is_numeric(other):
            return type(self).from_nanoseconds(int(self._nanoseconds // other))
        return NotImplemented

    def __bool__(self) -> bool:
        return self._nanoseconds != 0

    def __neg__(self) -> Self:
        return type(self).from_nanoseconds(-self._nanoseconds)

    def __abs__(self) -> Self:
        return type(self).from_nanoseconds(abs(self._nanoseconds))

    def __repr__(self) -> str:
        return f"{self.seconds} s"


class Clock:
    start_mono_ns: int
    offset_ns: int
    tags_ns: dict[str | None, int]
    start_time_ns: int
    __slots__ = ("offset_ns", "start_mono_ns", "start_time_ns", "tags_ns")

    @overload
    def __init__(self, seconds: int | None = None, nanoseconds: int | None = None) -> None: ...
    @overload
    def __init__(self, seconds: float) -> None: ...
    def __init__(self, seconds: float | int | None = None, nanoseconds: int | None = None) -> None:
        if nanoseconds is not None:
            self.start_time_ns = int(seconds or 0) * 1_000_000_000 + nanoseconds
        elif seconds is not None:
            self.start_time_ns = round(seconds * 1e9)
        else:
            self.start_time_ns = time.time_ns()
        self.start_mono_ns = time.monotonic_ns()
        self.offset_ns = self.start_time_ns - self.start_mono_ns
        self.tags_ns = {}

    @classmethod
    def from_time(cls, time: Time) -> Self:
        return cls(time.nanoseconds)

    @classmethod
    def from_nanoseconds(cls, nanoseconds: int) -> Self:
        return cls(nanoseconds=nanoseconds)

    @property
    def start_time(self) -> Time:
        return Time.from_nanoseconds(self.start_time_ns)

    def tag(self, label: str) -> None:
        self.tags_ns[label] = time.monotonic_ns()

    def elapsed_ns(self, label: str | None = None) -> int:
        mono = time.monotonic_ns()
        if label is None:
            return mono - self.start_mono_ns
        return mono - self.tags_ns[label]

    def elapsed(self, label: str | None = None) -> Duration:
        return Duration.from_nanoseconds(self.elapsed_ns(label))

    def elapsed_s(self, label: str | None = None) -> float:
        return self.elapsed_ns(label) / 1e9

    def get_elapsed_ns(self, label: str | None = None) -> int | None:
        with suppress(KeyError):
            return self.elapsed_ns(label)
        return None

    def get_elapsed_s(self, label: str | None = None) -> float | None:
        with suppress(KeyError):
            return self.elapsed_s(label)
        return None

    def get_elapsed(self, label: str | None = None) -> Duration | None:
        with suppress(KeyError):
            return self.elapsed(label)
        return None

    def tag_time_ns(self, label: str) -> int:
        return self.tags_ns[label] + self.offset_ns

    def tag_time_s(self, label: str) -> float:
        return self.tag_time_ns(label) / 1e9

    def tag_time(self, label: str) -> Time:
        return Time.from_nanoseconds(self.tag_time_ns(label))

    def get_tag_time_ns(self, label: str) -> int | None:
        with suppress(KeyError):
            return self.tag_time_ns(label)
        return None

    def get_tag_time_s(self, label: str) -> float | None:
        with suppress(KeyError):
            return self.tag_time_s(label)
        return None

    def get_tag_time(self, label: str) -> Time | None:
        with suppress(KeyError):
            return self.tag_time(label)
        return None

    def now_ns(self) -> int:
        return time.monotonic_ns() + self.offset_ns

    def now_s(self) -> float:
        return self.now_ns() / 1e9

    def now(self) -> Time:
        return Time.from_nanoseconds(self.now_ns())


class TimeUnit(Enum):
    """How to pace a ramp to regulation."""

    NANOSECOND = "nanosecond"
    MICROSECOND = "microsecond"
    MILLISECOND = "millisecond"
    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"

    @property
    def seconds(self) -> Positive:
        match self:
            case TimeUnit.NANOSECOND:
                return 1e-9
            case TimeUnit.MICROSECOND:
                return 1e-6
            case TimeUnit.MILLISECOND:
                return 0.001
            case TimeUnit.SECOND:
                return 1.0
            case TimeUnit.MINUTE:
                return 60.0
            case TimeUnit.HOUR:
                return 3600.0
            case TimeUnit.DAY:
                return 86400.0

    @property
    def nanoseconds(self) -> int:
        match self:
            case TimeUnit.NANOSECOND:
                return 1
            case TimeUnit.MICROSECOND:
                return 1_000
            case TimeUnit.MILLISECOND:
                return 1_000_000
            case TimeUnit.SECOND:
                return 1_000_000_000
            case TimeUnit.MINUTE:
                return 60_000_000_000
            case TimeUnit.HOUR:
                return 3_600_000_000_000
            case TimeUnit.DAY:
                return 86_400_000_000_000


@dataclass(frozen=True, slots=True)
class Rate:
    per: TimeUnit
    value: Positive

    @property
    def per_second(self) -> float:
        return self.value / self.per.seconds
