import asyncio
from collections.abc import Generator
from contextlib import contextmanager, suppress


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
