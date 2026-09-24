from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Thread

from flyball.foundation import Operator, Positive, Reading
from flyball.foundation.time import Clock, Duration
from flyball.model.controller import Controller
from flyball.rig import Rig

from .step import Activity, Step


class Prompted(Activity):
    """Fires when someone fires it: an operator prompt, or an external trigger."""

    __slots__ = ()


@dataclass(frozen=True)
class Prompt(Step, tag="prompt", primary="message"):
    """Pause the program until a person (or an external trigger) fires the named prompt.

    `name` is what it is fired by (`POST /api/activities/{name}/fire`), default
    `prompt`. `timeout` gives up and ends the program.
    """

    message: str
    name: str | None = None
    timeout: Duration | None = None

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity:
        timeout = None if self.timeout is None else float(self.timeout)
        return Prompted(timeout, name=self.name, message=self.message, clock=rig.clock)


class Sustained(Activity):
    """Fires when a test on one controller's readings passes."""

    __slots__ = ("controller", "test")

    name: str  # pyright: ignore[reportIncompatibleVariableOverride]  an activity registered under this

    def __init__(
        self,
        controller: Controller,
        test: Callable[[Reading], bool],
        timeout: Positive | None = None,
        name: str | None = None,
        message: str | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(
            timeout,
            name=name or f"sustained:{controller.name}",
            message=message or f"{controller.name} to pass {test.__name__}",
            clock=clock,
        )
        self.controller = controller
        self.test = test

    def _on_tick(self, controller: Controller, reading: Reading | None) -> None:
        # A reading with no value, or one at a limit (the true value may lie beyond it),
        # passes no test.
        if (
            reading is not None
            and reading.usable
            and reading.at_limit is None
            and self.test(reading)
        ):
            self.fire()

    def attach(self, rig: Rig) -> None:
        self.controller.attach_on_tick(self._on_tick)

    def detach(self, rig: Rig) -> None:
        self.controller.detach_on_tick(self._on_tick)


class Settled(Activity):
    """Fires once every controller has read within `within` of its setpoint, `count` times running.

    Each controller is judged against *its own* setpoint at the reading's
    instant, so one still on a ramp is measured against where the ramp is
    now. A reading outside the band resets that controller's count, and so
    does one with no value or one at a limit (the true value may lie beyond
    it); the controllers are independent, and the activity fires when the
    last of them arrives.
    """

    __slots__ = ("_controllers", "_counts", "count", "within")

    name: str  # pyright: ignore[reportIncompatibleVariableOverride]

    def __init__(
        self,
        controllers: list[Controller],
        within: float,
        count: int = 3,
        timeout: Positive | None = None,
        name: str | None = None,
        message: str | None = None,
        clock: Clock | None = None,
    ) -> None:
        names = ",".join(controller.name for controller in controllers)
        super().__init__(
            timeout,
            name=name or f"settle:{names}",
            message=message or f"{names} within {within:g} of setpoint for {count} readings",
            clock=clock,
        )
        self._controllers = list(controllers)
        self._counts = dict.fromkeys(self._controllers, 0)
        self.within = within
        self.count = count

    def _on_tick(self, controller: Controller, reading: Reading | None) -> None:
        if reading is None:
            return
        if not reading.usable or reading.at_limit is not None:
            self._counts[controller] = 0
            return
        setpoint = controller.setpoint_at(reading.time_ns)
        self._counts[controller] = (
            self._counts[controller] + 1 if abs(reading.value - setpoint) <= self.within else 0
        )
        if all(n >= self.count for n in self._counts.values()):
            self.fire()

    def attach(self, rig: Rig) -> None:
        for controller in self._controllers:
            controller.attach_on_tick(self._on_tick)

    def detach(self, rig: Rig) -> None:
        for controller in self._controllers:
            controller.detach_on_tick(self._on_tick)


class Timed(Activity):
    """Fires after `duration` seconds of the rig's clock: a timed wait, a soak, a ramp's end.

    On a scaled clock it passes proportionally sooner; on a stepped one it
    steps the clock past itself at once. `timeout`, like `Prompt`'s, ends the
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
