from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Thread

from flyball.foundation import Operator, Positive, Reading
from flyball.foundation.time import Clock, Duration
from flyball.model.controller import Controller
from flyball.rig import Rig

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
        if reading is not None and self.test(reading):
            self.fire()

    def attach(self, rig: Rig) -> None:
        self.controller.attach_on_tick(self._on_tick)

    def detach(self, rig: Rig) -> None:
        self.controller.detach_on_tick(self._on_tick)


class Arrived(Activity):
    """Fires once every controller has read within `within` of its setpoint, `readings` times.

    Each controller is judged against *its own* setpoint at the reading's
    instant, so one still on a ramp is measured against where the ramp is
    now. A reading outside the band resets that controller's count; the
    controllers are independent, and the activity fires when the last of
    them arrives.
    """

    __slots__ = ("_controllers", "_counts", "readings", "within")

    name: str  # pyright: ignore[reportIncompatibleVariableOverride]

    def __init__(
        self,
        controllers: list[Controller],
        within: float,
        readings: int = 3,
        timeout: Positive | None = None,
        name: str | None = None,
        message: str | None = None,
        clock: Clock | None = None,
    ) -> None:
        names = ",".join(controller.name for controller in controllers)
        super().__init__(
            timeout,
            name=name or f"arrive:{names}",
            message=message or f"{names} within {within:g} of setpoint for {readings} readings",
            clock=clock,
        )
        self._controllers = list(controllers)
        self._counts = dict.fromkeys(self._controllers, 0)
        self.within = within
        self.readings = readings

    def _on_tick(self, controller: Controller, reading: Reading | None) -> None:
        if reading is None:
            return
        setpoint = controller.setpoint_at(reading.time_ns)
        self._counts[controller] = (
            self._counts[controller] + 1 if abs(reading.value - setpoint) <= self.within else 0
        )
        if all(count >= self.readings for count in self._counts.values()):
            self.fire()

    def attach(self, rig: Rig) -> None:
        for controller in self._controllers:
            controller.attach_on_tick(self._on_tick)

    def detach(self, rig: Rig) -> None:
        for controller in self._controllers:
            controller.detach_on_tick(self._on_tick)


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
