from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, StrEnum
from threading import Event, Thread, current_thread
from time import monotonic
from typing import TYPE_CHECKING, Any, Self

from pydantic import GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from .typing import Positive

if TYPE_CHECKING:
    from .clock import Clock


class Labelled(StrEnum):
    """A string enum whose members carry a display label.

    Declare members as `NAME = "wire_value", "Display label"`; the label
    becomes the option's title in the JSON schema. Omitted, the value is used.
    """

    label: str

    def __new__(cls, value: str, label: str = "") -> Self:
        member = str.__new__(cls, value)
        member._value_ = value
        member.label = label or value
        return member

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        schema = handler(core)
        schema.pop("enum", None)
        schema["oneOf"] = [{"const": member.value, "title": member.label} for member in cls]
        return schema


class UnsetType(Enum):
    UNSET = "Unset"

    def __repr__(self) -> str:
        return "Unset"


Unset = UnsetType.UNSET


def validate_normalised(name: str, value: float) -> float:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0.0 and 1.0, got {value}")
    return value


def format_quantity(flow: float, units: str | None = None) -> str:
    return f"{flow:.3f}{f' {units}' if units else ''}"


def require[T](value: T | None, error: type[Exception], *args: Any, **kwargs: Any) -> T:
    if value is None:
        raise error(*args, **kwargs)
    return value


class PeriodicLoop:
    _loop_time: Positive
    _event: Event
    _fn: Callable[[], None]
    _thread: Thread | None = None
    _erroring: Exception | None = None
    _next_loop_time: float | None = None
    _stop_on_error: bool

    def __init__(
        self,
        fn: Callable,
        loop_time: Positive,
        stop_on_error: bool,
        *args,
        clock: Clock | None = None,
        **kwargs: Any,
    ) -> None:
        self._loop_time = loop_time
        self._clock = clock  # None: wall time
        self._handle: int | None = None
        self.missed = 0
        """Times the loop found itself more than a period behind and skipped ahead."""
        self._event = Event()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._stop_on_error = stop_on_error

    @property
    def running(self) -> bool:
        return self._handle is not None or (self._thread is not None and self._thread.is_alive())

    @property
    def loop_time(self) -> Positive:
        return self._loop_time

    def set_loop_time(self, loop_time: Positive) -> None:
        self._loop_time = loop_time

    def start(self) -> None:
        schedule = getattr(self._clock, "schedule", None)
        if schedule is not None:  # a stepped clock runs the loop itself, as time is advanced
            if self._handle is not None:
                raise RuntimeError("Loop is already running")
            self._handle = schedule(self._loop_time, self._once)
            return
        if self._thread is not None:
            if self._thread.is_alive():
                raise RuntimeError("Loop is already running")
            self._thread.join()
        self._event.clear()
        self._thread = Thread(target=self.run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = None, join: bool = True) -> None:
        """Stop; `join=False` from inside the loop's own function, which cannot wait for itself."""
        if self._handle is not None and self._clock is not None:
            self._clock.cancel(self._handle)  # type: ignore[attr-defined]
            self._handle = None
        if self._thread is not None:
            self._event.set()
            if join and self._thread is not current_thread():
                self._thread.join(timeout=timeout)

    def _once(self) -> None:
        try:
            self._fn(*self._args, **self._kwargs)
            self.set_ok()
        except Exception as e:
            self.set_error(e)
            if self._stop_on_error:
                self.stop()

    def set_error(self, error: Exception | None) -> None:
        self._erroring = error

    def set_ok(self) -> None:
        self._erroring = None

    def _now(self) -> float:
        return monotonic() if self._clock is None else self._clock.monotonic()

    def _wait(self, seconds: float) -> None:
        if self._clock is None:
            self._event.wait(timeout=seconds)
        else:
            self._clock.wait(self._event, timeout=seconds)

    def run(self) -> None:
        # On the rig's clock: a scaled clock polls proportionally faster.
        self._next_loop_time = self._now() + self.loop_time
        while not self._event.is_set():
            try:
                self._fn(*self._args, **self._kwargs)
                self.set_ok()
            except Exception as e:
                self.set_error(e)
                if self._stop_on_error:
                    break
            now = self._now()
            sleep_time = self._next_loop_time - now
            if sleep_time > 0:
                self._wait(sleep_time)
            elif sleep_time < -self.loop_time:
                # More than a period behind (a stall, a suspend): resynchronise
                # rather than fire back-to-back to repay the missed ones.
                self.missed += 1
                self._next_loop_time = now
            self._next_loop_time += self.loop_time
        self._thread = None


@dataclass(frozen=True, slots=True)
class WithWarning[T]:
    value: T
    warning: Exception | None = None

    @property
    def ok(self) -> bool:
        return self.warning is None

    def try_raise(self) -> None:
        if self.warning is not None:
            raise self.warning

    def __call__(self) -> T:
        return self.value
