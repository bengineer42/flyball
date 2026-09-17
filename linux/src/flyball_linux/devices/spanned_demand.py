"""A `[RPW]` demand that is a bare 0-1 fraction unless `unit`+`span` map it onto engineering units.

The shape `pwm_channel` and `mcp4725` both need verbatim: validate the pair, build the `drive`
signal spec, and convert between a signal's value and the fraction a device actually writes. Two
call sites today (`pwm.py`, `mcp4725.py`) with identical code, not just similar shape -- worth
sharing now rather than waiting for a third.
"""

from __future__ import annotations

from flyball.core.quantity import Quantity
from flyball.core.signal import Access, Band, Role, SignalSpec


def validate_span(unit: str | None, span: Band | None, *, prefix: str = "") -> None:
    """Raise if exactly one of `unit`/`span` is given, or `span` isn't rising.

    Raises:
        ValueError: `unit` and `span` don't go together, or `span` isn't `lo < hi`.
    """
    if (unit is None) != (span is None):
        raise ValueError(f"{prefix}`unit` and `span` go together")
    if span is not None and span[1] <= span[0]:
        raise ValueError(f"{prefix}span must be a rising pair, not {list(span)}")


def drive_spec(
    unit: str | None, quantity: str | None, span: Band | None, *, bare: Quantity
) -> SignalSpec:
    """The `drive` signal: `bare` (0-1) `[RPW]`, or `span` mapped onto `unit`/`quantity` if given.

    `bare` is the device's own dimensionless "fraction of full X" quantity (`pwm_channel`'s
    "fraction of full drive", `mcp4725`'s "fraction of full scale") -- a caller's choice, not
    this module's, since the two devices mean different physical things by "full".

    Call `validate_span` first -- this assumes the pair is already valid.
    """
    if unit is None:
        return SignalSpec(
            name="drive", quantity=bare, access=Access.RPW, role=Role.DEMAND,
            limits=(0.0, 1.0), initial=0.0,
        )
    assert span is not None  # `unit` and `span` go together, checked by `validate_span`
    return SignalSpec(
        name="drive",
        quantity=Quantity(quantity or "drive", unit),
        access=Access.RPW,
        role=Role.DEMAND,
        limits=span,
        initial=span[0],
    )


def to_fraction(value: float, span: Band | None) -> float:
    """The 0-1 fraction a `drive` value of `value` asks for: itself, or linear over `span`."""
    if span is None:
        return value
    d0, d1 = span
    return (value - d0) / (d1 - d0)


def from_fraction(achieved: float, span: Band | None) -> float:
    """The `drive`-unit value an achieved 0-1 fraction corresponds to: itself, or over `span`."""
    if span is None:
        return achieved
    d0, d1 = span
    return d0 + achieved * (d1 - d0)
