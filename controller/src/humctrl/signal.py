"""The wait primitive.

One concept: something fires, and says whether it was cancelled. Infrastructure
rather than an extension point -- user logic belongs in a
:class:`~humctrl.conditions.Condition`, which owns a signal.
"""

from threading import Event, Thread


class Signal(Event):
    """Fires when whatever owns it decides it is done.

    ``interrupted`` distinguishes a cancellation from the condition being met:
    an owner firing it for its own reasons calls ``Event.set`` directly, so only
    an outside caller ever sets the flag.
    """

    def __init__(self) -> None:
        super().__init__()
        self.interrupted = False

    def fire(self) -> None:
        """Report the condition met, without marking an interruption."""
        Event.set(self)

    def set(self) -> None:
        """Cancel, releasing anyone waiting."""
        self.interrupted = True
        Event.set(self)


class TimedSignal(Signal):
    """A signal that fires itself after ``duration`` seconds.

    The waiting thread blocks on the signal itself, so cancelling it wakes the
    thread immediately rather than leaving it parked for the full duration.
    """

    def __init__(self, duration: float) -> None:
        super().__init__()
        Thread(target=self._expire, args=(duration,), daemon=True).start()

    def _expire(self, duration: float) -> None:
        self.wait(duration)
        self.fire()
