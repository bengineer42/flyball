"""Tuning rules: a measured plant in, a set of gains out.

Each rule is stated in the ideal form its source uses and converted once by
:meth:`~flyball.autotune.types.Gains.of_ideal`, so the coefficients here can be
checked line by line against the literature without unpicking any algebra.

Two families, by what they need. :func:`imc` and :func:`amigo` take a fitted
:class:`~flyball.autotune.types.FOPDT` from a step test; :func:`ziegler_nichols`
and :func:`tyreus_luyben` take an :class:`~flyball.autotune.types.Ultimate` from
a relay test and need no model at all.
"""

from __future__ import annotations

from .errors import NoDeadTimeError
from .types import FOPDT, Gains, Ultimate


def imc(model: FOPDT, lam: float | None = None, derivative: bool = True) -> Gains:
    """IMC / lambda tuning: one dial, and it means something.

    ``lam`` is the closed-loop time constant you are asking for — how fast the
    loop should chase a setpoint change — so it is tuned in seconds against the
    process, not by feel. Smaller is faster and less tolerant of the model being
    wrong. It cannot usefully go below the dead time: nothing makes the plant
    respond before ``θ``.

    Recommended over the oscillation-based rules for this rig: the step test it
    needs is gentler on the pumps than driving a limit cycle, and the model it
    fits is worth having on its own.

    Args:
        model: The fitted plant.
        lam: Desired closed-loop time constant. Defaults to ``max(τ, 0.8θ)``,
            which is the conservative end — roughly no faster than the plant
            already is.
        derivative: Whether to include derivative action. PI is the safer choice
            on a noisy reading, and gives up little unless ``θ`` is large.

    Returns:
        The gains.
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
    """AMIGO (Åström & Hägglund): Ziegler-Nichols' replacement, PID form.

    Fitted to hold a bounded maximum sensitivity rather than to hit a decay
    ratio, so it detunes as the plant gets harder instead of ringing. Where
    Ziegler-Nichols is reliably too aggressive for a process with real dead time,
    this is not.

    Only the PID form is implemented. The published PI variant has coefficients
    that differ between the sources available, and an unverified transcription of
    a tuning rule is worse than no rule: use :func:`imc` with
    ``derivative=False`` for PI.

    Args:
        model: The fitted plant.

    Returns:
        The gains.

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
    """The classic closed-loop rule, targeting quarter-amplitude damping.

    Included because everything is compared against it, not because it is a good
    default: a quarter-decay response overshoots by roughly a quarter and sits
    close to the stability edge, which on a rig whose supply humidities drift is
    close enough to be a problem. Prefer :func:`tyreus_luyben` from the same
    measurement.

    Args:
        ultimate: The critical point from a relay test.
        derivative: Whether to include derivative action.

    Returns:
        The gains.
    """
    gain, period = ultimate.gain, ultimate.period
    if not derivative:
        return Gains.of_ideal(kp=0.45 * gain, ti=period / 1.2)
    return Gains.of_ideal(kp=0.6 * gain, ti=period / 2, td=period / 8)


def tyreus_luyben(ultimate: Ultimate, derivative: bool = True) -> Gains:
    """Ziegler-Nichols detuned for processes with real dead time.

    Roughly half the gain and twice the integral time, which buys a much larger
    stability margin for a slower approach to setpoint. The sane default if a
    relay test is all you have.

    Args:
        ultimate: The critical point from a relay test.
        derivative: Whether to include derivative action.

    Returns:
        The gains.
    """
    gain, period = ultimate.gain, ultimate.period
    if not derivative:
        return Gains.of_ideal(kp=gain / 3.2, ti=2.2 * period)
    return Gains.of_ideal(kp=gain / 2.2, ti=2.2 * period, td=period / 6.3)
