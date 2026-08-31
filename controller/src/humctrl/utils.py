import asyncio
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from enum import Enum
from threading import Event, Thread
from time import monotonic
from typing import Protocol

from humctrl.typing import Positive


class Config[T](Protocol):
    def build(self) -> T: ...


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


def require[T](value: T | None, error: type[Exception], *args, **kwargs) -> T:
    if value is None:
        raise error(*args, **kwargs)
    return value


class Topic[T]:
    """Fan-out to asyncio subscribers, publishable from any thread.

    Construct on the event loop that serves the subscribers. A subscriber that
    falls behind drops stale items rather than blocking the publisher.
    """

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._subscribers: set[asyncio.Queue[T]] = set()

    def publish(self, item: T) -> None:
        """Offer an item to every subscriber. Never blocks, never raises."""
        with suppress(RuntimeError):  # loop closed during shutdown
            self._loop.call_soon_threadsafe(self._fanout, item)

    def _fanout(self, item: T) -> None:
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()  # drop the stale item
            queue.put_nowait(item)

    @contextmanager
    def subscribe(self, maxsize: int = 1) -> Generator[asyncio.Queue[T]]:
        """A queue of the items published while subscribed.

        Holds at most ``maxsize`` items, dropping the oldest to make room. Pass
        0 to queue without limit, at the risk of growing behind a stuck reader.
        """
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

    def __init__(self, fn: Callable[[], None], loop_time: Positive, stop_on_error: bool = True):
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

    def set_loop_time(self, loop_time: Positive):
        self._loop_time = loop_time

    def start(self):
        if self._thread is not None:
            if self._thread.is_alive():
                raise RuntimeError("Loop is already running")
            self._thread.join()
        self._event.clear()
        self._thread = Thread(target=self.run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = None):
        if self._thread is not None:
            self._event.set()
            self._thread.join(timeout=timeout)

    def set_error(self, error: Exception | None):
        self._erroring = error

    def set_ok(self):
        self._erroring = None

    def run(self):
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
