"""The adaptive package against the simulated plants: identify, judge, retune.

The plants are the ones behind `examples/simulated/{oven,tank,chiller}.yaml`,
driven here in closed loop by the same PI gains those files carry, with the
setpoint stepped as a program would. What the identifier believes is checked
against what the plant was built with.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from flyball_sim.plant import Fopdt, Integrator, Lag, Noisy

from flyball.adaptive import (
    Bounds,
    Excitation,
    Identifier,
    ModelRejectedError,
    NotIdentifiedError,
    Plant,
    RecursiveLeastSquares,
    RegressorMismatchError,
    Sample,
    Schema,
    SelfTuner,
    Verdict,
)
from flyball.adaptive.types import Arx
from flyball.autotune.rules import imc
from flyball.autotune.types import FOPDT, Gains

SCHEMA = Schema(None, None)  # type: ignore[arg-type]  the signals are not consulted here


class PI:
    """A textbook PI whose correction is added to the setpoint; `lo`..`hi` bound the demand.

    Anti-windup by clamping: an increment that would push the demand past a
    limit is not kept.
    """

    def __init__(
        self, kp: float, ki: float, lo: float, hi: float, *, smart: bool = True, bias: float = 0.0
    ) -> None:
        self.kp, self.ki, self.lo, self.hi = kp, ki, lo, hi
        self.smart = smart  # the demand is in the reading's units: the setpoint is fed forward
        self.integral = bias

    def demand(self, setpoint: float, reading: float, dt: float) -> float:
        error = setpoint - reading
        self.integral += self.ki * error * dt
        demand = (setpoint if self.smart else 0.0) + self.kp * error + self.integral
        if not self.lo <= demand <= self.hi:
            self.integral -= self.ki * error * dt
            demand = min(max(demand, self.lo), self.hi)
        return demand


@dataclass
class Loop:
    """A plant under a PI, sampled at `dt`; `smart` means the demand is in the output's units."""

    plant: Noisy
    pi: PI
    dt: float
    setpoint: float
    feedforward: object = None  # demand -> plant input, for a smart drive
    identifier: Identifier | None = None
    tuner: SelfTuner[Gains] | None = None
    time: float = 0.0

    def tick(self) -> None:
        demand = self.pi.demand(self.setpoint, self.plant.output, self.dt)
        self.plant.input = self.feedforward(demand) if self.feedforward else demand  # type: ignore[operator]
        self.plant.step(self.dt)
        self.time += self.dt
        if self.identifier is not None:
            self.identifier.push(Sample(self.plant.output, demand))
        if self.tuner is not None:
            self.tuner.observe()
            self.tuner.elapsed(self.dt)

    def run(self, seconds: float, setpoint: float | None = None) -> None:
        if setpoint is not None:
            self.setpoint = setpoint
        for _ in range(round(seconds / self.dt)):
            self.tick()


def oven(delay_samples: int = 5) -> Loop:
    """`oven.yaml`: FOPDT, tau 60 s, dead 5 s, gain 80 over ambient 20, a smart drive in °C.

    Seen through the drive, the plant has unit gain: a demand of 50 °C holds 50 °C.
    """
    plant = Noisy(Fopdt(60.0, 5.0, gain=80.0, value=20.0, ambient=20.0), 0.05, seed=1)
    ident = Identifier(SCHEMA, interval=1.0, delay_samples=delay_samples)
    # The drive inverts the plant as built; a later change to the plant is not its to know.
    return Loop(plant, PI(0.02, 0.0005, 20, 100), 1.0, 20.0, lambda d: (d - 20.0) / 80.0, ident)


def chiller() -> Loop:
    """A lag driven raw: full drive pulls 30 °C below a 20 °C room, tau 120 s. Negative gain."""
    plant = Noisy(Lag(120.0, value=20.0, gain=-30.0, ambient=20.0), 0.1, seed=2)
    ident = Identifier(SCHEMA, interval=1.0, excitation=Excitation(threshold=0.05, hold=200))
    return Loop(plant, PI(-0.05, -0.002, 0.0, 1.0, smart=False, bias=0.5), 1.0, 20.0, None, ident)


def tank() -> Loop:
    """`tank.yaml`: an integrator against a drain, which is a lag of tau 1/leak = 20 s."""
    plant = Noisy(Integrator(gain=2.0, leak=0.05, value=10.0), 0.02, seed=1)
    ident = Identifier(SCHEMA, interval=0.5)
    return Loop(plant, PI(0.05, 0.01, 0, 40), 0.5, 10.0, lambda d: d * 0.05 / 2.0, ident)


def steps(loop: Loop, setpoints: list[float], hold_s: float) -> None:
    for setpoint in setpoints:
        loop.run(hold_s, setpoint)


# region Pieces


def test_rls_fits_a_line_exactly():
    rls = RecursiveLeastSquares(2, forgetting=1.0)
    for x in range(1, 30):
        rls.update([x, 1.0], 2.0 * x + 1.0)
    a, b = rls.parameters
    assert a == pytest.approx(2.0, rel=1e-4) and b == pytest.approx(1.0, rel=1e-3)
    assert rls.confidence > 0.0
    with pytest.raises(RegressorMismatchError):
        rls.update([1.0], 1.0)


def test_excitation_stays_open_for_the_hold_after_the_input_moves():
    gate = Excitation(window=3, threshold=1.0, hold=2)
    assert [gate.push(v) for v in (0.0, 0.0, 0.0)] == [False, False, False]
    assert gate.push(5.0), "moved"
    assert [gate.push(5.0) for _ in range(5)] == [True, True, True, False, False], (
        "the window still spans the step for two pushes, then the hold covers two more"
    )
    gate.reset()
    assert not gate.push(5.0), "after a reset a steady input is not excitement"


def test_arx_to_plant_carries_the_operating_point():
    arx = Arx(a=0.5, b=-15.0, offset=10.0, interval=1.0)
    plant = arx.plant()
    assert plant.gain == pytest.approx(-30.0) and plant.ambient == pytest.approx(20.0)
    with pytest.raises(ModelRejectedError, match="pole"):
        Arx(a=1.02, b=1.0).plant()


# endregion

# region Identification on the simulated plants


def test_the_oven_is_identified_from_setpoint_steps():
    loop = oven()
    with pytest.raises(NotIdentifiedError):
        loop.identifier.arx()  # type: ignore[union-attr]
    steps(loop, [50, 70, 40, 80], 900)
    plant = loop.identifier.plant()  # type: ignore[union-attr]
    assert plant.gain == pytest.approx(1.0, rel=0.1)
    assert plant.tau == pytest.approx(60.0, rel=0.15)
    assert plant.dead_time == 5.0 and abs(plant.ambient) < 5.0


def test_a_wrong_dead_time_lands_in_the_time_constant():
    """Dead time is not identified: given none, the lag absorbs it and tau comes out long.

    The gain is off too (1.22 against 1.0): the whole 5 s of dead time is
    unmodelled.
    """
    loop = oven(delay_samples=0)
    steps(loop, [50, 70, 40, 80], 900)
    plant = loop.identifier.plant()  # type: ignore[union-attr]
    assert plant.gain == pytest.approx(1.0, rel=0.25) and plant.tau > 65.0


def test_a_plant_resting_away_from_zero_is_identified_with_its_sign():
    """The chiller: negative gain, and it rests at 20 °C, which the offset term carries."""
    loop = chiller()
    steps(loop, [5, 0, 10, -5], 1800)
    plant = loop.identifier.plant()  # type: ignore[union-attr]
    assert plant.gain < 0 and plant.gain == pytest.approx(-30.0, rel=0.25)
    assert plant.tau == pytest.approx(120.0, rel=0.25)
    assert plant.ambient == pytest.approx(20.0, abs=3.0)
    # Feedforward inverts the model, operating point included: holding 5 °C takes about half drive.
    assert loop.identifier.feedforward(5.0) == pytest.approx(0.5, abs=0.1)  # type: ignore[union-attr]


def test_the_tank_is_a_lag_of_one_over_leak():
    loop = tank()
    steps(loop, [20, 30, 15, 35], 450)
    plant = loop.identifier.plant()  # type: ignore[union-attr]
    assert plant.gain == pytest.approx(1.0, rel=0.1) and plant.tau == pytest.approx(20.0, rel=0.1)


def test_noise_alone_does_not_pass_for_a_plant():
    """A gate below the input's noise lets every sample through; the fit is refused, not offered."""
    plant = Noisy(Lag(120.0, value=20.0, gain=-30.0, ambient=20.0), 0.1, seed=2)
    ident = Identifier(SCHEMA, interval=1.0, excitation=Excitation(threshold=0.001))
    pi = PI(-0.05, -0.002, 0.0, 1.0, smart=False, bias=0.5)
    loop = Loop(plant, pi, 1.0, 20.0, None, ident)
    tuner = SelfTuner(ident, rule=lambda p: p)
    loop.tuner = tuner
    loop.run(1800, 5.0)  # settle mid-range, then hold: nothing asks the loop to move
    ident.reset()
    loop.run(3600)
    assert ident.identified, "the gate let the noise through"
    assert tuner.consider().verdict in (Verdict.IMPLAUSIBLE, Verdict.UNCHANGED, Verdict.OFFERED)
    if tuner.consider().offered:
        # If a model did come out it is nowhere near the plant and Bounds would need tightening.
        assert not Bounds(gain=(10.0, 100.0)).holds(tuner.consider().plant)  # type: ignore[arg-type]


# endregion

# region The self-tuner


def rule(plant: Plant) -> Gains:
    return imc(FOPDT(plant.gain, plant.tau, plant.dead_time), derivative=False)


def test_a_retune_is_offered_once_then_held_until_the_model_drifts():
    loop = oven()
    tuner = SelfTuner(loop.identifier, rule)  # type: ignore[arg-type]
    loop.tuner = tuner
    assert tuner.consider().verdict is Verdict.UNIDENTIFIED
    steps(loop, [50, 70, 40, 80], 900)
    first = tuner.consider()
    assert first.offered and first.plant is not None
    gains = tuner.accept(first.plant)
    assert gains.kp > 0 and tuner.applied is first.plant
    assert tuner.consider().verdict is Verdict.TOO_SOON, "no retune inside the settling time"
    steps(loop, [60, 55], 900)
    assert tuner.consider().verdict is Verdict.UNCHANGED, "the plant did not move"


def test_a_changed_plant_is_refitted_and_offered():
    loop = oven()
    tuner = SelfTuner(loop.identifier, rule)  # type: ignore[arg-type]
    loop.tuner = tuner
    steps(loop, [50, 70, 40, 80], 900)
    tuner.accept(tuner.consider().plant)  # type: ignore[arg-type]
    before = tuner.applied
    assert before is not None
    # The oven's element loses half its power: the drive still inverts the old
    # model, so the same demand now holds half the rise -- a gain of 0.5 as seen.
    inner = loop.plant.plant
    assert isinstance(inner, Fopdt)
    inner._lag.gain = 40.0
    seen = []
    for setpoint in (60, 80, 50, 70, 60, 80):
        loop.run(900, setpoint)
        seen.append(tuner.consider().verdict)
    final = tuner.consider()
    assert final.plant is not None and final.plant.gain == pytest.approx(0.5, rel=0.25), seen
    assert final.verdict is Verdict.OFFERED, seen
    assert tuner.accept(final.plant).kp == pytest.approx(rule(before).kp * 2, rel=0.3), (
        "half the plant gain: about twice the controller gain"
    )


def test_divergence_is_a_rise_in_the_residual_s_level_not_one_bad_sample():
    ident = Identifier(SCHEMA, settle=1)
    tuner = SelfTuner(ident, rule, residual_growth=3.0)
    tuner.accept(Plant(gain=1.0, tau=60.0))

    def fitted(residual: float) -> None:
        ident._seen += 1
        ident.residual = residual
        tuner.observe()

    for _ in range(SelfTuner.WARM_UP + 10):
        fitted(0.1)  # a model that fits to within its noise
    fitted(2.0)  # one bad sample: not a verdict
    assert tuner.residual_level < 0.3 and not tuner._diverging()
    for _ in range(10):
        fitted(1.0)  # the errors stay big: the plant has changed
    assert tuner._diverging()
    tuner.observe()  # a tick with nothing fitted changes nothing
    assert tuner._diverging()
    tuner.accept(Plant(gain=1.0, tau=60.0))  # a fresh model starts the comparison over
    assert not tuner._diverging()


def test_a_sign_flip_or_a_wild_fit_is_implausible():
    ident = Identifier(SCHEMA)
    tuner = SelfTuner(ident, rule, bounds=Bounds(gain=(0.5, 2.0), tau=(10.0, 100.0)))
    tuner.accept(Plant(gain=1.0, tau=60.0))
    tuner._since = 1e9  # well past the settling time
    ident._seen = ident.settle  # identified, by fiat
    ident._rls._parameters = [0.9835, -0.0165, 0.0]  # gain -1: the wrong way round
    assert tuner.consider().verdict is Verdict.IMPLAUSIBLE
    ident._rls._parameters = [0.5, 5.0, 0.0]  # gain 10: outside the bounds
    assert tuner.consider().verdict is Verdict.IMPLAUSIBLE
    ident._rls._parameters = [0.9835, 0.0165, 0.0]  # as applied
    assert tuner.consider().verdict is Verdict.UNCHANGED


def test_the_offered_gains_hold_the_oven():
    """End to end: identify, retune by IMC, and the loop settles on a fresh step."""
    loop = oven()
    tuner = SelfTuner(loop.identifier, rule)  # type: ignore[arg-type]
    loop.tuner = tuner
    steps(loop, [50, 70, 40, 80], 900)
    gains = tuner.accept(tuner.consider().plant)  # type: ignore[arg-type]
    loop.pi = PI(gains.kp, gains.ki, 20, 100)
    loop.run(600, 60.0)
    trace = []
    loop.setpoint = 75.0
    for _ in range(600):
        loop.tick()
        trace.append(loop.plant.plant.output)
    assert max(trace) < 75.0 + 3.0, "under a fifth of the step in overshoot"
    assert abs(trace[-1] - 75.0) < 0.5, "settled within 10 minutes"
    assert trace[180] > 70.0, "and got there in about three time constants"


# endregion
