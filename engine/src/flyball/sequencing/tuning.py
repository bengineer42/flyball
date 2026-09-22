"""Autotune as a command: measure one loop, fit a plant, store the gains.

`Tune` is an ordinary [Command][flyball.sequencing.command.Command], so
registering it gives two surfaces from one class: a program step
(`tune: {loop: ..., save_as: ...}`) and a one-shot through
`POST /api/programs/command`, which the programmer runs on its own thread.
Interrupting, progress and the generated form come with that -- none of it is
written here.

The experiment runs open-loop, as [flyball.autotune.experiments][] says to: the
target is moved directly and reaches the plant through the controller's own
feedforward, so the gain measured is the one the trim loop will see.

Nothing is persisted. The fitted tuning is added to `rig.tunings`, where a
following `regulate` step can name it; `PUT /api/history/tunings/{name}` is how
it reaches the store, because a `Command` is handed the `Rig` and the `Rig`
holds no `Store`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from flyball.autotune import FOPDT, Gains, StepTest, amigo, imc
from flyball.autotune.errors import AutotuneError
from flyball.control.laws import OpenLoop, SmithPredictor
from flyball.foundation import Operator, Reading
from flyball.foundation.time import Clock, Duration
from flyball.library.tunings import Tuning
from flyball.model.controller import Controller, ControllerMode
from flyball.model.feedforward import Setpoint
from flyball.model.law import ControlLaw
from flyball.rig import Rig

from .command import Activity, Command

RULES = ("imc", "amigo")
"""The rules a `tune:` step may name. Both take a fitted FOPDT from a step test."""

type Law = Literal["pi", "pid", "smith"]

LAWS: tuple[Law, ...] = ("pi", "pid", "smith")
"""The laws a `tune:` step may fit. `pi`/`pid` apply `rule` to the whole plant; `smith`
wraps a [SmithPredictor][flyball.control.laws.SmithPredictor] around a PI fit to the
delay-free plant, ignoring `rule` -- see `_smith_tuning`."""

_STEP_FRACTION = 0.1
"""Of the source signal's range, when `size` is not given: a tenth is large enough
to clear sensor noise and small enough to stay inside a working span."""

DEFAULT_WINDOW = Duration(seconds=60)
"""How long a plateau must hold when `window` is not given. A module constant rather
than an inline default: `Duration` is immutable, and a call in a dataclass default is
evaluated once anyway."""

_BAND_FRACTION = 0.05
"""Of the step, when `band` is not given. `band` must sit above the noise and well
below the step; a twentieth of the step is the usual compromise."""


def _default_size(controller: Controller) -> float:
    """A step a tenth of the source's range, signed away from whichever end is nearer.

    Raises:
        ValueError: If the signal declares no range to take a fraction of.
    """
    span = controller.source.range
    if span is None:
        raise ValueError(
            f"controller {controller.name!r} reads {controller.source.address}, which declares"
            " no range or limits, so `size` cannot be defaulted; give it in the step"
        )
    low, high = span
    size = abs(high - low) * _STEP_FRACTION
    reading = controller.last_value
    if reading is None:
        return size
    # Step away from the nearer end, so a loop resting near a limit is not
    # asked for a target it cannot reach.
    return -size if reading - low > high - reading else size


def _gains(model: FOPDT, rule: str, lam: float | None, derivative: bool) -> Gains:
    """Apply the named rule to `model`.

    Raises:
        ValueError: If `rule` is not one of `RULES`.
    """
    if rule == "imc":
        return imc(model, lam=lam, derivative=derivative)
    if rule == "amigo":
        return amigo(model)
    raise ValueError(f"unknown tuning rule {rule!r}; it is one of {list(RULES)}")


def _smith_tuning(model: FOPDT, controller: Controller, lam: float | None, tag: str) -> Tuning:
    """A [SmithPredictor][flyball.control.laws.SmithPredictor] sized to `model`.

    `kp`/`ki`/`tt` are IMC's PI for the *delay-free* plant (`dead_time=0`): the predictor
    compensates the real dead time itself, so tuning against it again would double-count
    it, per `SmithPredictor`'s own docstring. `gain`/`tau`/`dead_time` carry straight
    through as measured. `feedforward` reads off the controller this tuning is for -- 1.0
    under the `setpoint` feedforward (a drive in the reading's units), 0.0 under any other
    (`none`, `affine`, ...), matching what `SmithPredictor` says it needs: a raw drive, so
    its own model sees the whole demand as its input.
    """
    pi = imc(FOPDT(model.gain, model.tau, 0.0), lam=lam, derivative=False)
    feedforward = 1.0 if isinstance(controller.feedforward, Setpoint) else 0.0
    law = SmithPredictor.config(
        kp=pi.kp,
        ki=pi.ki,
        gain=model.gain,
        tau=model.tau,
        dead_time=model.dead_time,
        tt=pi.tt,
        feedforward=feedforward,
    )
    return Tuning(tag=tag, config=law)


class Tuned(Activity):
    """Drives a step test on one controller; fires once a tuning has been stored.

    Each reading is handed to the experiment and the target it asks for is
    commanded, so the run is paced by the source signal, not by a clock of its
    own. On completion the model is fitted, the rule applied, and the gains
    added to `rig.tunings` under `save_as` -- all inside the tick callback,
    because `fire()` only releases the waiter and carries no result.

    `detach` puts the loop back the way it was found. It runs on completion,
    failure and interruption alike (`Programmer._wait_out` brackets the wait in
    a `finally`), so a stopped tune does not leave a controller open-loop at a
    stepped target.
    """

    __slots__ = (
        "_law",
        "_mode",
        "_reference",
        "_rig",
        "controller",
        "gains",
        "lam",
        "law",
        "model",
        "rule",
        "save_as",
        "test",
    )

    name: str  # pyright: ignore[reportIncompatibleVariableOverride]  registered under this

    def __init__(
        self,
        controller: Controller,
        test: StepTest,
        save_as: str,
        rule: str = "imc",
        lam: float | None = None,
        law: Law = "pi",
        message: str | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(
            None,
            name=f"tune:{controller.name}",
            message=message
            or f"{controller.name}: step {test.size:+g} from {test.base:g}, then fit",
            clock=clock,
        )
        self.controller = controller
        self.test = test
        self.save_as = save_as
        self.rule = rule
        self.lam = lam
        self.law = law
        self.model: FOPDT | None = None
        self.gains: Gains | None = None
        self._rig: Rig | None = None
        self._mode: ControllerMode | None = None
        self._law: ControlLaw | None = None
        self._reference: float | None = None

    def _on_tick(self, controller: Controller, reading: Reading | None) -> None:
        if reading is None or self.test.done:
            return
        try:
            target = self.test.step(controller.to_law_time(reading.time_ns), reading.value)
        except AutotuneError as error:
            self.fail(error)
            return
        if self.test.done:
            self._store()
        else:
            controller.set_reference(target)

    def _store(self) -> None:
        """Fit, tune, add the tuning to the rig, and release the waiter."""
        try:
            self.model = self.test.result
            if self.law == "smith":
                tuning = _smith_tuning(self.model, self.controller, self.lam, self.save_as)
            else:
                self.gains = _gains(self.model, self.rule, self.lam, self.law == "pid")
                tuning = self.gains.to_tuning(self.save_as)
        except (AutotuneError, ValueError) as error:
            self.fail(error)
            return
        if self._rig is not None:
            self._rig.tunings.add(tuning)
        self.fire()

    def attach(self, rig: Rig) -> None:
        self._rig = rig
        controller = self.controller
        self._mode = controller.mode
        self._law = controller.law
        # A generator (a ramp in flight) is not a value to come back to; only a
        # plain setpoint is restored, and anything else lands in manual.
        reference = controller.reference
        self._reference = reference if isinstance(reference, float) else None
        controller.regulate(self.test.base, tuning=OpenLoop.config())
        controller.attach_on_tick(self._on_tick)

    def detach(self, rig: Rig) -> None:
        controller = self.controller
        controller.detach_on_tick(self._on_tick)
        if self._law is not None:
            controller.set_law(self._law)
        if self._mode is ControllerMode.REGULATING and self._reference is not None:
            controller.regulate(self._reference)
        else:
            controller.manual()


@dataclass(frozen=True)
class Tune(Command, tag="tune", primary="loop"):
    """Step one loop open-loop, fit a plant to the response, and store the gains.

    The loop is left as it was found, so a following `regulate` naming
    `save_as` is what puts the new tuning to work:

        - tune: {loop: heaters.heater2, save_as: zone2}
        - regulate: {loop: heaters.heater2, setpoint: 300, tuning: zone2}

    One controller, never a list: two experiments at once on a shared plant
    contaminate each other's responses, and the fit would mean nothing. Three
    furnace zones are three `tune:` steps.

    `timeout` is seconds and applies per plateau, not to the whole run; it is
    not a `Duration` because `window` is already this command's one
    flat-foldable field and a second would make the flat form ambiguous.

    Args:
        loop: The controller to tune, by target address; None for the rig's default.
        save_as: The tag the fitted tuning is stored under.
        size: Signed step, in the source's units. Defaults to a tenth of the
            source's range, directed away from whichever end is nearer.
        base: The target to settle at first. Defaults to the current reading.
        window: How long the reading must hold still to count as a plateau.
            Must exceed the dead time, or the flat stretch before the response
            reads as a plateau.
        band: How much the reading may move within `window`. Defaults to a
            twentieth of the step. Above the sensor noise, well below `size`.
        rule: `imc` or `amigo`; ignored when `law` is `smith`, which always
            fits IMC to the delay-free plant (the predictor supplies its own
            dead-time compensation, so `amigo` -- which divides by the
            measured dead time -- does not apply).
        lam: IMC's closed-loop time constant, in seconds. Defaults to about as
            fast as the plant already is.
        law: `pi`, `pid`, or `smith`. `pi`/`pid` apply `rule` to the whole
            fitted plant; `smith` wraps a `SmithPredictor` around a PI fit to
            the plant's lag alone -- worth it when the dead time is
            comparable to the time constant, below that `pi`/`pid` does as
            well.
        timeout: Seconds allowed per plateau; None waits for ever.
    """

    loop: str | None = None
    save_as: str = "fitted"
    size: float | None = None
    base: float | None = None
    window: Duration = DEFAULT_WINDOW
    band: float | None = None
    rule: str = "imc"
    lam: float | None = None
    law: Law = "pi"
    timeout: float | None = None
    message: str | None = None

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        if self.rule not in RULES:
            raise ValueError(f"unknown tuning rule {self.rule!r}; it is one of {list(RULES)}")
        if self.law not in LAWS:
            raise ValueError(f"unknown tuning law {self.law!r}; it is one of {list(LAWS)}")
        controller = rig.controllers.resolve(self.loop)
        base = self.base if self.base is not None else controller.last_value
        if base is None:
            raise ValueError(
                f"controller {controller.name!r} has no reading yet to step from;"
                " give `base` or wait for the source to publish"
            )
        size = self.size if self.size is not None else _default_size(controller)
        if not size:
            raise ValueError("`size` cannot be zero: the reading would never move")
        test = StepTest(
            base=base,
            size=size,
            window=self.window.seconds,
            band=self.band if self.band is not None else abs(size) * _BAND_FRACTION,
            timeout=self.timeout,
        )
        return Tuned(
            controller,
            test,
            save_as=self.save_as,
            rule=self.rule,
            lam=self.lam,
            law=self.law,
            message=self.message,
            clock=rig.clock,
        )

    def missing(self, rig: Rig) -> list[str]:
        if self.loop is None:
            if rig.controllers.default is None:
                return ["the rig has no default controller"]
        elif self.loop not in rig.controllers:
            return [f"controller {self.loop!r} is not on the rig"]
        return []


__all__ = ["Tune", "Tuned"]
