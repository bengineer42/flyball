"""A `[RPW]` signal that is a bare 0-1 fraction unless `unit`+`span` map it onto engineering units.

Shared by any demand-signal driver that writes a bare 0-1 fraction to its bus unless `unit`+`span`
map it onto engineering units first: validate the pair, build the signal spec, and convert between
a signal's value and the fraction actually written. Originally `pwm.py` and `mcp4725.py`
(`extensions/linux`), promoted here once `extensions/chips` needed it too -- `mcp4725` moved out of
`flyball_linux`, `pwm.py` stayed, and neither package should depend on the other for this.
"""

from __future__ import annotations

from flyball.foundation.device import Access, Bounds, Role, SignalSpec
from flyball.foundation.quantities import Quantity


def validate_span(unit: str | None, span: Bounds | None, *, prefix: str = "") -> None:
    """Raise if exactly one of `unit`/`span` is given, or `span` isn't rising.

    Raises:
        ValueError: `unit` and `span` don't go together, or `span` isn't `lo < hi`.
    """
    if (unit is None) != (span is None):
        raise ValueError(f"{prefix}`unit` and `span` go together")
    if span is not None and span[1] <= span[0]:
        raise ValueError(f"{prefix}span must be a rising pair, not {list(span)}")


def spanned_signal_spec(
    name: str,
    unit: str | None,
    quantity: str | None,
    span: Bounds | None,
    *,
    bare: Quantity,
    off_at_zero: bool = False,
) -> SignalSpec:
    """A `[RPW]` demand signal called `name`: `bare` (0-1), or `span` mapped onto `unit`.

    `bare` is the caller's own dimensionless "fraction of full X" quantity (`pwm_channel`'s
    "fraction of full drive", `mcp4725`'s "fraction of full scale") -- a caller's choice, not
    this module's, since different devices mean different physical things by "full".

    `off_at_zero` is the caller saying 0 % is its output's inactive level (a PWM duty, not a
    DAC's 0 V, which is a setpoint): the spec then declares `off` -- 0, or `span[0]` -- so a
    stop writes it. Never when the span straddles 0, where `span[0]` is full reverse (an
    H-bridge, a Peltier). Default: no `off`, and a stop leaves the output as it is.

    Call `validate_span` first -- this assumes the pair is already valid.
    """
    if unit is None:
        return SignalSpec(
            name=name,
            quantity=bare,
            access=Access.RPW,
            role=Role.DEMAND,
            limits=(0.0, 1.0),
            initial=0.0,
            off=0.0 if off_at_zero else None,
        )
    assert span is not None  # `unit` and `span` go together, checked by `validate_span`
    straddles = span[0] < 0.0 < span[1]
    return SignalSpec(
        name=name,
        quantity=Quantity(quantity or name, unit),
        access=Access.RPW,
        role=Role.DEMAND,
        limits=span,
        initial=span[0],
        off=span[0] if off_at_zero and not straddles else None,
    )


def to_fraction(value: float, span: Bounds | None) -> float:
    """The 0-1 fraction a signal value of `value` asks for: itself, or linear over `span`."""
    if span is None:
        return value
    d0, d1 = span
    return (value - d0) / (d1 - d0)


def from_fraction(achieved: float, span: Bounds | None) -> float:
    """The signal-unit value an achieved 0-1 fraction corresponds to: itself, or over `span`."""
    if span is None:
        return achieved
    d0, d1 = span
    return d0 + achieved * (d1 - d0)


__all__ = ["from_fraction", "spanned_signal_spec", "to_fraction", "validate_span"]
