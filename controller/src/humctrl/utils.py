import asyncio
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import Enum, StrEnum
from inspect import signature
from threading import Event, Thread
from time import monotonic
from typing import Any, Self, get_type_hints

from pydantic import BeforeValidator, GetJsonSchemaHandler, create_model
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from humctrl.typing import Positive


def keyed_by(field: str) -> BeforeValidator:
    """Accept a list of items and key it by ``field``.

    A mapping passes through untouched, so a config file may write either form.
    Items may be raw mappings or already-built objects.

    Args:
        field: The attribute or key to use as the mapping key.

    Returns:
        A validator to put in an ``Annotated`` alias.

    Raises:
        ValueError: If two items share a key. Silently keeping the last would
            drop a tuning the file plainly asked for.
    """

    def to_mapping(value: Any) -> Any:
        if not isinstance(value, list):
            return value
        mapping = {
            item[field] if isinstance(item, dict) else getattr(item, field): item for item in value
        }
        if len(mapping) != len(value):
            raise ValueError(f"duplicate {field} in list")
        return mapping

    return BeforeValidator(to_mapping)


class Labelled(StrEnum):
    """A string enum whose members carry a display label.

    Declare members as ``NAME = "wire_value", "Display label"``. The label goes
    into the JSON schema as a per-option title, so a form can show it without a
    second copy of the options on the client. Omit it and the value is used.
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


class Topic[T]:
    """Fan-out to asyncio subscribers, publishable from any thread.

    Subscribe from the event loop that serves the subscribers; publish from any
    thread. Publishing before the first subscriber is a no-op, so the owner need
    not be constructed on the loop. A subscriber that falls behind drops stale
    items rather than blocking the publisher.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue[T]] = set()

    def publish(self, item: T) -> None:
        """Offer an item to every subscriber. Never blocks, never raises."""
        loop = self._loop  # read once: a subscriber may bind it on the loop thread
        if loop is None:
            return  # nobody has subscribed yet, so there is nowhere to deliver
        with suppress(RuntimeError):  # loop closed during shutdown
            loop.call_soon_threadsafe(self._fanout, item)

    def _fanout(self, item: T) -> None:
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()  # drop the stale item
            queue.put_nowait(item)

    @contextmanager
    def subscribe(self, maxsize: int = 1) -> Generator[asyncio.Queue[T]]:
        """A queue of the items published while subscribed.

        Args:
            maxsize: How many items to hold, dropping the oldest to make room.
                0 queues without limit, at the risk of growing behind a stuck
                reader.

        Yields:
            The queue, for as long as the context is open.
        """
        self._loop = asyncio.get_running_loop()
        queue: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)


class PeriodicLoop:
    _loop_time: Positive
    _event: Event
    _fn: Callable[[], None]
    _thread: Thread | None = None
    _erroring: Exception | None = None
    _next_loop_time: float | None = None
    _stop_on_error: bool

    def __init__(
        self, fn: Callable[[], None], loop_time: Positive, stop_on_error: bool = True
    ) -> None:
        self._loop_time = loop_time
        self._event = Event()
        self._fn = fn
        self._stop_on_error = stop_on_error

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def loop_time(self) -> Positive:
        return self._loop_time

    def set_loop_time(self, loop_time: Positive) -> None:
        self._loop_time = loop_time

    def start(self) -> None:
        if self._thread is not None:
            if self._thread.is_alive():
                raise RuntimeError("Loop is already running")
            self._thread.join()
        self._event.clear()
        self._thread = Thread(target=self.run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._event.set()
            self._thread.join(timeout=timeout)

    def set_error(self, error: Exception | None) -> None:
        self._erroring = error

    def set_ok(self) -> None:
        self._erroring = None

    def run(self) -> None:
        self._next_loop_time = monotonic() + self.loop_time
        while not self._event.is_set():
            try:
                self._fn()
                self.set_ok()
            except Exception as e:
                self.set_error(e)
                if self._stop_on_error:
                    break
            sleep_time = self._next_loop_time - monotonic()
            if sleep_time > 0:
                self._event.wait(timeout=sleep_time)
            self._next_loop_time += self.loop_time
        self._thread = None


def to_list[T](item: T | list[T] | None) -> list[T]:
    if item is None:
        return []
    if isinstance(item, list):
        return item
    return [item]


def all_to_list[T](*args: T | list[T] | None) -> list[T]:
    return [item for arg in args for item in to_list(arg)]


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


def creation_model(
    cls: type,
    name: str | None = None,
    suffix: str | None = "Args",
    base=None,
    extra: dict[str, Any] | None = None,
):
    """The pydantic model for constructing ``cls``, taken from its signature.

    One field per constructor parameter, keeping its annotation and default, so
    a class that can be built can also be described, validated and sent over the
    wire without the fields being written twice.

    Args:
        cls: The class whose ``__init__`` defines the fields.
        name: Model name stem. Defaults to ``cls.__name__``.
        suffix: Appended to the stem, e.g. ``"Config"``.
        base: Model to inherit from, for shared behaviour and ``isinstance``.
        extra: Fields to add beyond the constructor's own, as
            ``{name: (annotation, default)}``.

    Returns:
        The generated model.

    Raises:
        TypeError: If ``cls`` takes ``*args`` or ``**kwargs``, which have no
            field names to derive.
    """
    hints = get_type_hints(cls.__init__)
    fields: dict[str, Any] = {}
    for field, parameter in signature(cls).parameters.items():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            raise TypeError(f"{cls.__name__} takes *args/**kwargs; no schema can be derived")
        fields[field] = (
            hints.get(field, Any),
            ... if parameter.default is parameter.empty else parameter.default,
        )
    fields.update(extra or {})
    return create_model((name or cls.__name__) + (suffix or ""), __base__=base, **fields)


class ModelOf:
    """A model on the class, an instance of it on the instance.

    Accessed through the owning class the descriptor returns the model itself,
    so its schema is reachable without constructing anything. Accessed through
    an instance it reads ``names`` off that instance and returns a populated
    model.
    """

    def __init__(self, model: type, names: tuple[str, ...]) -> None:
        self._model = model
        self._names = names

    @property
    def model(self) -> type:
        """The model this descriptor hands out."""
        return self._model

    def __get__(self, obj: Any, owner: type | None = None) -> Any:
        if obj is None:
            return self._model
        return self._model(**{name: getattr(obj, name) for name in self._names})
