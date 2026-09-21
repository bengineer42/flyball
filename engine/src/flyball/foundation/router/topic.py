import asyncio
from collections.abc import Generator
from contextlib import contextmanager, suppress


class Topic[T]:
    """Fan-out to asyncio subscribers, publishable from any thread.

    Subscribe from the event loop; publish from anywhere. Publishing with no
    subscriber is a no-op. A subscriber that falls behind drops stale items
    rather than blocking the publisher.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue[T]] = set()

    @property
    def subscribed(self) -> bool:
        """Whether anyone is listening -- check before building an expensive item."""
        return bool(self._subscribers)

    def publish(self, item: T) -> None:
        """Offer an item to every subscriber. Never blocks, never raises."""
        loop = self._loop  # read once: a subscriber may bind it on the loop thread
        if loop is None or not self._subscribers:
            return  # nobody listening, so there is nowhere to deliver
        with suppress(RuntimeError):  # loop closed during shutdown
            loop.call_soon_threadsafe(self._fanout, item)

    def _fanout(self, item: T) -> None:
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()  # drop the stale item
            queue.put_nowait(item)

    @contextmanager
    def subscribe(self, maxsize: int = 1) -> Generator[asyncio.Queue[T]]:
        """A queue of the items published while the context is open.

        Args:
            maxsize: How many items to hold, dropping the oldest to make room;
                0 is unbounded.
        """
        self._loop = asyncio.get_running_loop()
        queue: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)
            if not self._subscribers:
                self._loop = None  # so publish is free again until the next subscriber


class Latest[K, V]:
    """The newest value per key, for readers that poll at their own rate.

    The writer does one dict store per update, so a loop at any rate costs the
    same. A reader asks for what changed since the version it last saw and gets
    at most one value per key. Each reader keeps its own version.
    """

    def __init__(self) -> None:
        self._values: dict[K, tuple[int, V]] = {}
        self._version = 0
        self._watchers = 0

    @property
    def watched(self) -> bool:
        """Whether anyone is reading -- check before building an expensive value."""
        return self._watchers > 0

    def set(self, key: K, value: V) -> None:
        """Record the newest value for `key`. Never blocks, never raises."""
        self._version += 1
        self._values[key] = (self._version, value)

    def get(self, key: K) -> V | None:
        """The newest value for `key`, or None."""
        held = self._values.get(key)
        return None if held is None else held[1]

    def discard(self, key: K) -> None:
        self._values.pop(key, None)

    @property
    def version(self) -> int:
        return self._version

    def changed_since(self, version: int) -> tuple[int, dict[K, V]]:
        """Every key updated after `version` (0 for all), and the version to ask from next.

        A key stored during the call may be missed now but is caught next time.
        """
        current = self._version
        changed = {key: value for key, (at, value) in list(self._values.items()) if at > version}
        return current, changed

    @contextmanager
    def watch(self) -> Generator[None]:
        """Count a reader in, so writers know a value is worth building."""
        self._watchers += 1
        try:
            yield
        finally:
            self._watchers -= 1
