from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Thread
from typing import Any

from flyball.core import Channel, Operator, Positive, Reading
from flyball.core.clock import Clock, Duration
from flyball.core.sink import Observer
from flyball.runtime.rig import Rig

from .command import Activity, Command


class Prompt(Activity):
    """Fires when someone fires it: an operator prompt, or an external trigger."""

    __slots__ = ()


@dataclass(frozen=True)
class Wait(Command, tag="wait", primary="message"):
    """Pause the program until the named signal is fired.

    `name` is what it is fired by (`POST /api/signals/{name}/fire`), default
    `wait`. `timeout` gives up and ends the program.
    """

    message: str
    name: str | None = None
    timeout: Duration | None = None

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity:
        timeout = None if self.timeout is None else float(self.timeout)
        return Prompt(timeout, name=self.name, message=self.message, clock=rig.clock)


class Sustained(Activity, Observer[Reading]):
    """Fires when a test on one channel's readings passes."""

    __slots__ = ("channel", "test")

    name: str  # pyright: ignore[reportIncompatibleVariableOverride]  an observer is always named

    def __init__(
        self,
        channel: Channel,
        test: Callable[[Reading], bool],
        timeout: Positive | None = None,
        name: str | None = None,
        message: str | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(
            timeout,
            name=name or f"sustained:{channel.name}",
            message=message or f"{channel.name} to pass {test.__name__}",
            clock=clock,
        )
        self.channel = channel
        self.test = test
        self.observes = frozenset((channel,))

    def observe(self, sample: Reading) -> None:
        if self.test(sample):
            self.fire()

    def attach(self, rig: Rig) -> None:
        rig.attach_observer(self)

    def detach(self, rig: Rig) -> None:
        rig.detach_observer(self)


class Arrived(Activity, Observer[Reading]):
    """Fires once every loop has read within `within` of its setpoint `readings` times running.

    Each loop is judged against *its own* setpoint at the reading's instant,
    so a loop still on a ramp is measured against where the ramp is now. A
    reading outside the band resets that loop's count; the loops are
    independent, and the activity fires when the last of them arrives.
    """

    __slots__ = ("_counts", "_loops", "readings", "within")

    name: str  # pyright: ignore[reportIncompatibleVariableOverride]

    def __init__(
        self,
        loops: list[tuple[Channel, Any]],
        within: float,
        readings: int = 3,
        timeout: Positive | None = None,
        name: str | None = None,
        message: str | None = None,
        clock: Clock | None = None,
    ) -> None:
        names = ",".join(loop.name for _, loop in loops)
        super().__init__(
            timeout,
            name=name or f"arrive:{names}",
            message=message or f"{names} within {within:g} of setpoint for {readings} readings",
            clock=clock,
        )
        self._loops = dict(loops)
        self._counts = dict.fromkeys(self._loops, 0)
        self.within = within
        self.readings = readings
        self.observes = frozenset(self._loops)

    def observe(self, sample: Reading) -> None:
        channel = sample.channel
        loop = self._loops[channel]
        setpoint = loop.setpoint_at(sample.time_ns)
        self._counts[channel] = (
            self._counts[channel] + 1 if abs(sample.value - setpoint) <= self.within else 0
        )
        if all(count >= self.readings for count in self._counts.values()):
            self.fire()

    def attach(self, rig: Rig) -> None:
        rig.attach_observer(self)

    def detach(self, rig: Rig) -> None:
        rig.detach_observer(self)


class Timed(Activity):
    """Fires after `duration` seconds of the rig's clock: a hold, a soak, a ramp's end.

    On a scaled clock it passes proportionally sooner; on a stepped one it
    steps the clock past itself at once. `timeout`, like `Wait`'s, ends the
    program instead if `duration` itself never elapses.
    """

    __slots__ = ("_thread", "duration_s")

    def __init__(
        self,
        duration_s: float,
        name: str | None = None,
        message: str | None = None,
        timeout: float | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(timeout, name=name, message=message or f"{duration_s:g} s", clock=clock)
        self.duration_s = duration_s
        self._thread: Thread | None = None

    def attach(self, rig: Rig) -> None:
        def run() -> None:
            # wait() on the signal's own event: an interrupt ends it early. A
            # wait that raises fails the step rather than leaving it pending.
            try:
                if not rig.clock.wait(self._event, self.duration_s):
                    self.fire()
            except Exception as error:
                self.fail(error)

        self._clock = rig.clock
        self._thread = Thread(target=run, daemon=True, name=f"timed:{self.name}")
        self._thread.start()
