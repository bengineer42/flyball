from threading import Event, Thread


class Signal(Event):
    """Fires when its condition is met.

    ``interrupted`` distinguishes a cancellation from the condition being met:
    whatever fires it for its own reasons goes through ``Event.set`` directly,
    so only an outside caller sets the flag.
    """

    def __init__(self) -> None:
        super().__init__()
        self.interrupted = False

    def step(self, value: float, time: float) -> None:
        """Offered every reading. Ignored unless the condition needs one."""

    def set(self) -> None:
        """Cancel, releasing anyone waiting."""
        self.interrupted = True
        Event.set(self)


class TimedSignal(Event):
    """An event that sets itself after ``duration``.

    Setting it from outside cancels the wait, and ``interrupted`` says which
    happened: the duration elapsing sets the flag without going through
    :meth:`set`.
    """

    def __init__(self, duration: float) -> None:
        super().__init__()
        Thread(target=self._expire, args=(duration,), daemon=True).start()

    def _expire(self, duration: float) -> None:
        self.wait(duration)
        Event.set(self)
