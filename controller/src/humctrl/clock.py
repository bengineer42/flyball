from __future__ import annotations

import time
from typing import Self, TypeGuard, overload

# if TYPE_CHECKING:
#     from _typeshed import ConvertibleToInt


type Numeric = float | int

type Tm = Numeric | TimeBase

type Td = Numeric | Duration


def normalise_parts(seconds: int, nanoseconds: int) -> tuple[int, int]:
    return divmod(seconds * 1_000_000_000 + nanoseconds, 1_000_000_000)


def is_numeric(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def to_nanoseconds(value: Tm) -> int:
    if isinstance(value, TimeBase):
        return value.as_nanoseconds
    if is_numeric(value):
        return round(value * 1e9)
    raise TypeError(f"Cannot convert {value!r} to nanoseconds")


def _duration_ns(value: Td) -> int | None:
    if isinstance(value, Duration):
        return value.as_nanoseconds
    if is_numeric(value):
        return round(value * 1e9)
    return None


class TimeBase:
    _seconds: int = 0
    _nanoseconds: int = 0

    @overload
    def __init__(self, seconds: int, nanoseconds: int) -> None: ...
    @overload
    def __init__(self, seconds: float | int) -> None: ...
    @overload
    def __init__(self, *, nanoseconds: int) -> None: ...

    def __init__(self, seconds: Numeric = 0, nanoseconds: int | None = None) -> None:
        if nanoseconds is not None:
            self.set_parts(int(seconds), nanoseconds)
        elif is_numeric(seconds):
            self.set_from_seconds(seconds)
        else:
            raise TypeError(f"Invalid arguments for {type(self).__name__} constructor")

    @classmethod
    def from_nanoseconds(cls, nanoseconds: int) -> Self:
        time = cls.__new__(cls)
        time.set_from_nanoseconds(nanoseconds)
        return time

    @classmethod
    def from_seconds(cls, seconds: float | int) -> Self:
        return cls.from_nanoseconds(round(seconds * 1e9))

    @classmethod
    def from_parts(cls, seconds: int, nanoseconds: int) -> Self:
        time = cls.__new__(cls)
        time.set_parts(seconds, nanoseconds)
        return time

    @property
    def seconds(self) -> int:
        return self._seconds

    @property
    def nanoseconds(self) -> int:
        return self._nanoseconds

    @property
    def parts(self) -> tuple[int, int]:
        return self._seconds, self._nanoseconds

    @property
    def as_nanoseconds(self) -> int:
        return self._seconds * 1_000_000_000 + self._nanoseconds

    @property
    def as_seconds(self) -> float:
        return self._seconds + self._nanoseconds / 1e9

    def set_nanoseconds(self, nanoseconds: int) -> None:
        self._seconds, self._nanoseconds = normalise_parts(self._seconds, nanoseconds)

    def set_parts(self, seconds: int, nanoseconds: int) -> None:
        self._seconds, self._nanoseconds = normalise_parts(seconds, nanoseconds)

    def set_seconds(self, seconds: int) -> None:
        self._seconds = int(seconds)

    def set_from_nanoseconds(self, nanoseconds: int) -> None:
        self._seconds, self._nanoseconds = normalise_parts(0, nanoseconds)

    def set_from_seconds(self, seconds: float) -> None:
        self.set_from_nanoseconds(round(seconds * 1e9))

    def _is_compatible(self, other: object) -> TypeGuard[Tm]:
        return isinstance(other, (type(self), int, float)) and not isinstance(
            other, bool
        )

    def __eq__(self, other: object) -> bool:
        if not self._is_compatible(other):
            return NotImplemented
        return self.as_nanoseconds == to_nanoseconds(other)

    def __hash__(self) -> int:
        return hash(self.as_nanoseconds)

    def __lt__(self, other: Tm) -> bool:
        if not self._is_compatible(other):
            return NotImplemented
        return self.as_nanoseconds < to_nanoseconds(other)

    def __le__(self, other: Tm) -> bool:
        if not self._is_compatible(other):
            return NotImplemented
        return self.as_nanoseconds <= to_nanoseconds(other)

    def __gt__(self, other: Tm) -> bool:
        if not self._is_compatible(other):
            return NotImplemented
        return self.as_nanoseconds > to_nanoseconds(other)

    def __ge__(self, other: Tm) -> bool:
        if not self._is_compatible(other):
            return NotImplemented
        return self.as_nanoseconds >= to_nanoseconds(other)

    def __float__(self) -> float:
        return self.as_seconds

    def __int__(self) -> int:
        return int(self.as_seconds)

    def __copy__(self) -> Self:
        return type(self).from_parts(self._seconds, self._nanoseconds)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._seconds}, {self._nanoseconds})"

    def __str__(self) -> str:
        nanoseconds = self.as_nanoseconds
        sign = "-" if nanoseconds < 0 else ""
        seconds, nanoseconds = divmod(abs(nanoseconds), 1_000_000_000)
        return f"{sign}{seconds}.{nanoseconds:09d}s"


class Time(TimeBase):
    def __add__(self, other: Td) -> Self:
        nanoseconds = _duration_ns(other)
        if nanoseconds is None:
            return NotImplemented
        return type(self).from_nanoseconds(self.as_nanoseconds + nanoseconds)

    __radd__ = __add__

    @overload
    def __sub__(self, other: Td) -> Self: ...
    @overload
    def __sub__(self, other: Time) -> Duration: ...
    def __sub__(self, other: Td | Time) -> Self | Duration:
        if isinstance(other, Time):
            return Duration.from_nanoseconds(self.as_nanoseconds - other.as_nanoseconds)
        nanoseconds = _duration_ns(other)
        if nanoseconds is None:
            return NotImplemented
        return type(self).from_nanoseconds(self.as_nanoseconds - nanoseconds)


class Duration(TimeBase):
    def __add__(self, other: Td) -> Self:
        nanoseconds = _duration_ns(other)
        if nanoseconds is None:
            return NotImplemented
        return type(self).from_nanoseconds(self.as_nanoseconds + nanoseconds)

    __radd__ = __add__

    def __sub__(self, other: Td) -> Self:
        nanoseconds = _duration_ns(other)
        if nanoseconds is None:
            return NotImplemented
        return type(self).from_nanoseconds(self.as_nanoseconds - nanoseconds)

    def __rsub__(self, other: Numeric) -> Self:
        nanoseconds = _duration_ns(other)
        if nanoseconds is None:
            return NotImplemented
        return type(self).from_nanoseconds(nanoseconds - self.as_nanoseconds)

    def __mul__(self, other: float | int) -> Self:
        return type(self).from_nanoseconds(round(self.as_nanoseconds * other))

    __rmul__ = __mul__

    @overload
    def __truediv__(self, other: Duration) -> float: ...
    @overload
    def __truediv__(self, other: float | int) -> Self: ...

    def __truediv__(self, other: Td) -> float | Self:
        if isinstance(other, Duration):
            return self.as_nanoseconds / other.as_nanoseconds
        if is_numeric(other):
            return type(self).from_nanoseconds(round(self.as_nanoseconds / other))
        return NotImplemented

    @overload
    def __floordiv__(self, other: Duration) -> int: ...
    @overload
    def __floordiv__(self, other: float | int) -> Self: ...
    def __floordiv__(self, other: Td) -> int | Self:
        if isinstance(other, Duration):
            return self.as_nanoseconds // other.as_nanoseconds
        if is_numeric(other):
            return type(self).from_nanoseconds(int(self.as_nanoseconds // other))
        return NotImplemented

    def __bool__(self) -> bool:
        return self.nanoseconds != 0 or self.seconds != 0

    def __neg__(self) -> Self:
        return type(self).from_nanoseconds(-self.as_nanoseconds)

    def __abs__(self) -> Self:
        return type(self).from_nanoseconds(abs(self.as_nanoseconds))

    def __repr__(self) -> str:
        return f"{self.as_seconds} s"


class Clock:
    offset_ns: int

    @overload
    def __init__(
        self, seconds: int | None = None, nanoseconds: int | None = None
    ) -> None: ...
    @overload
    def __init__(self, seconds: float) -> None: ...
    def __init__(
        self, seconds: float | int | None = None, nanoseconds: int | None = None
    ) -> None:
        if nanoseconds is not None:
            now_ns = int(seconds or 0) * 1_000_000_000 + nanoseconds
        elif seconds is not None:
            now_ns = round(seconds * 1e9)
        else:
            now_ns = time.time_ns()
        self.offset_ns = now_ns - time.monotonic_ns()

    @classmethod
    def from_time(cls, time: Time) -> Self:
        return cls(time.as_nanoseconds)

    @classmethod
    def from_nanoseconds(cls, nanoseconds: int) -> Self:
        return cls(nanoseconds=nanoseconds)

    def now_ns(self) -> int:
        return time.monotonic_ns() + self.offset_ns

    def now(self) -> float:
        return self.now_ns() / 1e9
