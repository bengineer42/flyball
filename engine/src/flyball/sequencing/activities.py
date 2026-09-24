from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Thread

from flyball.foundation import Operator, Positive, Reading
from flyball.foundation.device import Criterion, InputBinding, OnNoValue
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


class CriterionMet(Activity):
    """Fires once a signal's readings have met a criterion `count` times running.

    The signal is followed through an input binding the rig resolved at the
    step's start ([Rig.follow][flyball.rig.rig.Rig.follow]); each reading it
    brings is judged, on the delivery thread. One that does not meet the
    criterion resets the count, and so does one at a limit (the true value
    may lie beyond it). A reading with no value because of a fault meets it
    only under `on_no_value: fire`; otherwise it is not met, and the step's
    `timeout` decides. `pending` and `not_applicable` never meet it.

    With `from_start`, the base is the signal's value at the step's start; with
    none then (or one at a limit), the first reading that has one sets it and
    is not judged itself.

    It watches from when it is made, under the rig's lock with the step, so no
    reading between the step's start and its wait is missed: one that meets
    the criterion then fires it before it is attached. `detach` (the wait's
    end) and `release` (an activity ended before its wait) stop watching and
    unbind.
    """

    __slots__ = ("_base", "_binding", "_met", "_unwatch", "count", "criterion")

    name: str  # pyright: ignore[reportIncompatibleVariableOverride]

    def __init__(
        self,
        binding: InputBinding,
        criterion: Criterion,
        count: int = 3,
        timeout: Positive | None = None,
        name: str | None = None,
        message: str | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(
            timeout,
            name=name or f"settle:{criterion.signal}",
            message=message or f"{criterion.describe()} for {count} readings",
            clock=clock,
        )
        self._binding = binding
        self.criterion = criterion
        self.count = count
        self._met = 0
        self._base = self._base_from(binding.reading) if criterion.from_start else None
        self._unwatch: Callable[[], None] | None = binding.watch(self._on_reading)

    @staticmethod
    def _base_from(reading: Reading | None) -> float | None:
        """A reading's value, if it can be a base: a number with no caveat `at_limit`."""
        if reading is None or not reading.usable or reading.at_limit is not None:
            return None
        value = reading.value
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def binding(self) -> InputBinding:
        return self._binding

    @property
    def base(self) -> float | None:
        """The signal's value at the start, with `from_start`; None until there is one."""
        return self._base

    def _on_reading(self, binding: InputBinding) -> None:
        reading = binding.reading
        if reading is None:
            return
        if reading.usable and reading.at_limit is not None:
            self._met = 0
            return
        if self.criterion.from_start and self._base is None and reading.usable:
            self._base = self._base_from(reading)
            return
        base = 0.0 if self._base is None else self._base
        if self.criterion.met(reading, base, default=OnNoValue.IGNORE):
            self._met += 1
        else:
            self._met = 0
        if self._met >= self.count:
            self.fire()

    def detach(self, rig: Rig) -> None:
        self.release(rig)

    def release(self, rig: Rig) -> None:
        if self._unwatch is not None:
            self._unwatch()
            self._unwatch = None
        rig.unbind(self._binding)


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
