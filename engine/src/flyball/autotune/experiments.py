"""The two identification experiments, as state machines fed readings.

Neither owns a clock, a thread or the rig: each takes `(time, reading)` and
returns the target to command, so it runs inside any existing loop, including
one over a simulated plant.

Run them with the law set to [OpenLoop][flyball.control.laws.OpenLoop] and the
target moved directly. The step then passes through the application's actuator
arithmetic, so the fitted gain is in the same units the trim will use and stays
valid when the supply changes.
"""

from __future__ import annotations

from math import pi
from statistics import fmean

from flyball.foundation.typing import Positive

from .errors import ExperimentIncompleteError, ExperimentTimeoutError, ResponseTooSmallError
from .fit import SteadyState, fit_fopdt
from .types import FOPDT, Sample, Ultimate


def _check_timeout(started: float | None, time: float, timeout: float | None, phase: str) -> None:
    """Raise if `phase` has run longer than `timeout`; `None` for either disables the check.

    Raises:
        ExperimentTimeoutError: If the phase has overrun.
    """
    if timeout is not None and started is not None and time - started > timeout:
        raise ExperimentTimeoutError(phase, timeout)


class StepTest:
    """Hold, step, hold; fit a plant model to the response between the plateaus.

    The gentler experiment: the rig only moves between two steady targets, and
    the model is reusable for simulation and re-tuning without another run.

    Args:
        base: The target to settle at before stepping.
        size: Signed step. Well above the noise, within the loop's working range.
        window: How long the reading must hold still to count as a plateau. Must
            exceed the dead time, or the flat stretch before the response reads
            as a plateau.
        band: How much the reading may move within `window`. Above the noise,
            well below `size`.
        timeout: How long to allow each plateau. `None` waits forever.
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
        """Advance by one reading; return the target to command until the next.

        Raises:
            ExperimentTimeoutError: If the current plateau outruns `timeout`.
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
        # reading sits at `initial` for θ seconds after the step, and with a
        # window shorter than that it would otherwise settle on the spot.
        # Two bands, not one: a single band of movement is exactly what
        # `settled` tolerates, so a plateau differing from `initial` by one
        # band carries no information. Drift left over from the first plateau
        # reaches it unaided, and the fit then describes the drift -- a plant
        # of tau 60 s and dead time 40 s fits as gain 0.005, tau 3.9, dead 0.
        if settled and abs(self._steady.mean - self._initial) > 2 * self._steady.band:
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

    The oscillation's amplitude and period give `Ku` and `Tu` directly, with no
    model in between, at the cost of deliberately cycling the rig.

    `hysteresis` stops the relay chattering on noise; the describing-function
    estimate of `Ku` includes the correction for it. Expect `Ku` 10-20% low:
    the estimate keeps only the first harmonic and a lag-dominated limit cycle
    is nearer triangular than sinusoidal. That errs towards detuning, which is
    why a step test with [imc][flyball.autotune.rules.imc] is preferred where
    possible. Wide hysteresis also stretches the period, so keep it just above
    the noise.

    Args:
        centre: The reading to oscillate about.
        amplitude: The relay's half-swing `d`, in target units.
        hysteresis: Half-width `h` of the dead band, in reading units. Above the
            peak-to-peak noise, below the oscillation amplitude achieved.
        cycles: Usable cycles to average over; the first is always discarded.
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
        """Advance by one reading; return the target to command until the next.

        Raises:
            ExperimentTimeoutError: If the test outruns `timeout`.
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
