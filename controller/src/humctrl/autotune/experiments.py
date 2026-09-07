"""The two experiments, as state machines a caller pushes readings into.

Neither owns a clock, a thread, or the rig. Each takes ``(time, reading)`` and
returns the target to command, so it drops into whatever loop already exists and
nothing here has to know how the pumps are reached — which also means both can
be run against a simulated plant in a test without a single stub.

Run them through the *feedforward* path: the law set to
:class:`~humctrl.controller.OpenLoop` so ``correction`` stays at zero, and the
target moved directly. That is deliberate. The step then travels the same route
the trim will, through
:func:`~humctrl.controller.calculate_wet_fraction`, which has already divided out
the ``wet - dry`` span — so the gain that comes back is near 1 and the resulting
gains stay valid when the supply humidities change.
"""

from __future__ import annotations

from math import pi
from statistics import fmean

from humctrl.typing import Positive

from .errors import ExperimentIncompleteError, ExperimentTimeoutError, ResponseTooSmallError
from .fit import SteadyState, fit_fopdt
from .types import FOPDT, Sample, Ultimate


def _check_timeout(started: float | None, time: float, timeout: float | None, phase: str) -> None:
    """Raise if ``phase`` has been running longer than it is allowed to.

    Args:
        started: When the phase began, or ``None`` if it has not.
        time: Now.
        timeout: How long the phase may take. ``None`` disables the check.
        phase: The name to report.

    Raises:
        ExperimentTimeoutError: If the phase has overrun.
    """
    if timeout is not None and started is not None and time - started > timeout:
        raise ExperimentTimeoutError(phase, timeout)


class StepTest:
    """Hold, step, hold. Fits a plant model to the response between the plateaus.

    The gentler of the two experiments and the one to reach for first: the rig
    only ever moves between two steady targets, and the model it produces is
    reusable — for feedforward sizing, for simulation, for re-tuning later at a
    different ``lam`` without touching the hardware again.

    Args:
        base: The target to settle at before stepping.
        size: How far to step. Signed. Big enough to clear the noise by a good
            margin, small enough to stay in the range the loop will work over.
        window: How long the reading must hold still to count as a plateau. Must
            comfortably exceed the dead time, or the flat stretch before the
            response even starts reads as a plateau.
        band: How much the reading may move within that window. Above the sensor
            noise, well below ``size``.
        timeout: How long to allow each plateau before giving up. ``None`` waits
            forever, which is right for a manual run and wrong for an automated
            one.
    """

    def __init__(
        self,
        base: float,
        size: float,
        window: Positive,
        band: Positive,
        timeout: float | None = None,
    ) -> None:
        self.base = base
        self.size = size
        self.timeout = timeout
        self._steady = SteadyState(window, band)
        self._samples: list[Sample] = []
        self._started: float | None = None
        self._stepped: float | None = None
        self._initial = 0.0
        self._result: FOPDT | None = None

    @property
    def target(self) -> float:
        """The target to command right now."""
        return self.base if self._stepped is None else self.base + self.size

    @property
    def stepped(self) -> bool:
        """Whether the first plateau was reached and the step applied."""
        return self._stepped is not None

    @property
    def done(self) -> bool:
        """Whether the experiment has produced a model."""
        return self._result is not None

    @property
    def result(self) -> FOPDT:
        """The fitted plant.

        Raises:
            ExperimentIncompleteError: Before the second plateau is reached.
        """
        if self._result is None:
            raise ExperimentIncompleteError("StepTest")
        return self._result

    def step(self, time: float, reading: float) -> float:
        """Advance the experiment by one reading.

        Args:
            time: When the reading was taken.
            reading: What it read.

        Returns:
            The target to command until the next reading.

        Raises:
            ExperimentTimeoutError: If the current plateau takes longer than
                ``timeout`` to arrive.
        """
        if self._result is not None:
            return self.target
        if self._started is None:
            self._started = time

        settled = self._steady.push(time, reading)
        if self._stepped is None:
            if settled:
                self._initial = self._steady.mean
                self._stepped = time
                self._started = time
                self._steady.reset()
            else:
                _check_timeout(self._started, time, self.timeout, "settle")
            return self.target

        self._samples.append(Sample(time, reading))
        # A plateau that has not moved is the dead time, not the response: the
        # reading sits at ``initial`` for θ seconds after the step, and with a
        # window shorter than that it would otherwise settle on the spot.
        if settled and abs(self._steady.mean - self._initial) > self._steady.band:
            self._result = fit_fopdt(
                self._samples,
                start=self._stepped,
                initial=self._initial,
                final=self._steady.mean,
                size=self.size,
            )
        else:
            _check_timeout(self._started, time, self.timeout, "respond")
        return self.target


class RelayTest:
    """Bang-bang the target and read the critical point off the limit cycle.

    No model in between: the relay drives a sustained oscillation whose amplitude
    and period give ``Ku`` and ``Tu`` directly. Useful when a clean open-loop
    step is impractical, at the cost of deliberately cycling the rig — which for
    two DC pumps is wear you have chosen to spend.

    ``hysteresis`` is what makes it work on a real reading. Without it the relay
    chatters on sensor noise and the measured period is meaningless; with it the
    describing-function estimate of ``Ku`` picks up a correction term, which is
    applied here.

    Expect ``Ku`` to come out low by 10-20%. The estimate keeps only the first
    harmonic, and a limit cycle on a lag-dominated plant is nearer triangular
    than sinusoidal, so the measured peak overstates the fundamental. The error
    is towards detuning, which is the safe direction, but it is why a step test
    and :func:`~humctrl.autotune.rules.imc` beat this where both are possible.
    Wide hysteresis also stretches the measured period, so keep it just above
    the noise rather than comfortably above it.

    Args:
        centre: The reading to oscillate about.
        amplitude: The relay's half-swing ``d``, in target units.
        hysteresis: Half-width ``h`` of the dead band, in reading units. Set it
            above the peak-to-peak noise. Must stay below the oscillation
            amplitude the rig actually achieves.
        cycles: How many usable cycles to average over. The first is discarded as
            warm-up regardless.
        timeout: How long to allow the whole test.
    """

    def __init__(
        self,
        centre: float,
        amplitude: Positive,
        hysteresis: float = 0.0,
        cycles: int = 4,
        timeout: float | None = None,
    ) -> None:
        self.centre = centre
        self.amplitude = amplitude
        self.hysteresis = hysteresis
        self.cycles = cycles
        self.timeout = timeout
        self._high = True
        self._extreme = 0.0
        self._peaks: list[float] = []
        self._troughs: list[float] = []
        self._switches: list[float] = []
        self._started: float | None = None
        self._result: Ultimate | None = None

    @property
    def target(self) -> float:
        """The target to command right now: the centre plus or minus the swing."""
        return self.centre + (self.amplitude if self._high else -self.amplitude)

    @property
    def done(self) -> bool:
        """Whether enough cycles have been measured."""
        return self._result is not None

    @property
    def result(self) -> Ultimate:
        """The measured critical point.

        Raises:
            ExperimentIncompleteError: Before enough cycles have completed.
        """
        if self._result is None:
            raise ExperimentIncompleteError("RelayTest")
        return self._result

    def step(self, time: float, reading: float) -> float:
        """Advance the experiment by one reading.

        Args:
            time: When the reading was taken.
            reading: What it read.

        Returns:
            The target to command until the next reading.

        Raises:
            ExperimentTimeoutError: If the test outruns ``timeout``.
            ResponseTooSmallError: If the oscillation never clears the dead band.
        """
        if self._result is not None:
            return self.target
        if self._started is None:
            self._started = time
            self._high = reading < self.centre
            self._extreme = reading

        previous = self._high
        if reading < self.centre - self.hysteresis:
            self._high = True
        elif reading > self.centre + self.hysteresis:
            self._high = False

        if self._high is not previous:
            # The reading peaks *after* the relay switches, carried past the band
            # by the dead time, so each extreme belongs to the phase just ending:
            # driving up ends at a trough, driving down ends at a peak.
            (self._troughs if previous else self._peaks).append(self._extreme)
            self._switches.append(time)
            self._extreme = reading
            self._evaluate()

        self._extreme = min(self._extreme, reading) if self._high else max(self._extreme, reading)

        if self._result is None:
            _check_timeout(self._started, time, self.timeout, "oscillate")
        return self.target

    def _evaluate(self) -> None:
        """Close out the critical point once enough cycles are in hand."""
        wanted = self.cycles + 1  # the first of each is warm-up
        if len(self._peaks) < wanted or len(self._troughs) < wanted:
            return

        amplitude = (fmean(self._peaks[1:]) - fmean(self._troughs[1:])) / 2
        if amplitude <= self.hysteresis:
            raise ResponseTooSmallError(
                f"oscillation amplitude {amplitude:.3g} is within the "
                f"{self.hysteresis:.3g} hysteresis band"
            )

        switches = self._switches[1:]
        period = fmean(
            later - earlier for earlier, later in zip(switches, switches[2:], strict=False)
        )
        # Describing function for a relay of half-swing d against a dead band h.
        # With h = 0 this is the familiar Ku = 4d/(pi*a).
        effective = (amplitude**2 - self.hysteresis**2) ** 0.5
        self._result = Ultimate(gain=4 * self.amplitude / (pi * effective), period=period)
