"""Tuning rules: a measured plant in, gains out.

Each rule is stated in the ideal form its source uses and converted once by
[Gains.of_ideal][flyball.autotune.types.Gains.of_ideal], so coefficients can be
checked against the literature line by line.

[imc][flyball.autotune.rules.imc] and [amigo][flyball.autotune.rules.amigo] take
a fitted [FOPDT][flyball.autotune.types.FOPDT] from a step test;
[ziegler_nichols][flyball.autotune.rules.ziegler_nichols] and
[tyreus_luyben][flyball.autotune.rules.tyreus_luyben] take an
[Ultimate][flyball.autotune.types.Ultimate] from a relay test.
"""

from __future__ import annotations

from .errors import NoDeadTimeError
from .types import FOPDT, Gains, Ultimate


def imc(model: FOPDT, lam: float | None = None, derivative: bool = True) -> Gains:
    """IMC / lambda tuning.

    `lam` is the closed-loop time constant asked for, in seconds. Smaller is
    faster and less tolerant of model error; below the dead time it gains
    nothing. Preferred rule: the step test is gentler than a limit cycle and
    the model is reusable.

    Args:
        model: The fitted plant.
        lam: Closed-loop time constant. Defaults to `max(τ, 0.8θ)`, about as
            fast as the plant already is.
        derivative: Include derivative action. PI is safer on a noisy reading
            and gives up little unless `θ` is large.
    """
    tau, dead_time = model.tau, model.dead_time
    if lam is None:
        lam = max(tau, 0.8 * dead_time)
    if not derivative:
        return Gains.of_ideal(kp=tau / (model.gain * (lam + dead_time)), ti=tau)
    ti = tau + dead_time / 2
    return Gains.of_ideal(
        kp=ti / (model.gain * (lam + dead_time / 2)),
        ti=ti,
        td=tau * dead_time / (2 * tau + dead_time) if dead_time else 0.0,
    )


def amigo(model: FOPDT) -> Gains:
    """AMIGO (Åström & Hägglund), PID form.

    Holds a bounded maximum sensitivity rather than a decay ratio, so it detunes
    as the plant gets harder instead of ringing. PID only: published PI
    coefficients differ between sources, so use [imc][flyball.autotune.rules.imc]
    with `derivative=False` for PI.

    Raises:
        NoDeadTimeError: If the model has no dead time, which the rule divides by.
    """
    tau, dead_time = model.tau, model.dead_time
    if dead_time <= 0.0:
        raise NoDeadTimeError("AMIGO")
    return Gains.of_ideal(
        kp=(0.2 + 0.45 * tau / dead_time) / model.gain,
        ti=dead_time * (0.4 * dead_time + 0.8 * tau) / (dead_time + 0.1 * tau),
        td=0.5 * dead_time * tau / (0.3 * dead_time + tau),
    )


def ziegler_nichols(ultimate: Ultimate, derivative: bool = True) -> Gains:
    """Ziegler-Nichols closed-loop rule, targeting quarter-amplitude damping.

    Included as the baseline, not a default: it overshoots by about a quarter
    and sits near the stability edge. Prefer
    [tyreus_luyben][flyball.autotune.rules.tyreus_luyben] from the same
    measurement.
    """
    gain, period = ultimate.gain, ultimate.period
    if not derivative:
        return Gains.of_ideal(kp=0.45 * gain, ti=period / 1.2)
    return Gains.of_ideal(kp=0.6 * gain, ti=period / 2, td=period / 8)


def tyreus_luyben(ultimate: Ultimate, derivative: bool = True) -> Gains:
    """Ziegler-Nichols detuned for dead time: about half the gain, twice the integral time.

    A larger stability margin for a slower approach. The default when a relay
    test is all there is.
    """
    gain, period = ultimate.gain, ultimate.period
    if not derivative:
        return Gains.of_ideal(kp=gain / 3.2, ti=2.2 * period)
    return Gains.of_ideal(kp=gain / 2.2, ti=2.2 * period, td=period / 6.3)
