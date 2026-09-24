"""Errors the units package raises.

Classified as in [flyball.foundation.errors][flyball.foundation.errors].
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flyball.foundation.errors import FlyballError, NotFoundError, UnachievableError

if TYPE_CHECKING:
    from .dimension import Unit


class UnitError(FlyballError):
    """Base for everything flyball.foundation.quantities raises."""


class UnitNotFoundError(UnitError, NotFoundError):
    def __init__(self, symbol: str) -> None:
        super().__init__(f"No unit with symbol {symbol!r}")


class DimensionMismatchError(UnitError, UnachievableError):
    """A conversion between units of different dimensions. Unachievable, not malformed."""

    def __init__(self, source: Unit, target: Unit) -> None:
        self.source = source
        self.target = target
        super().__init__(
            f"cannot convert {source} to {target}: "
            f"{source.dimension.describe()} vs {target.dimension.describe()}"
        )


class CurveNotInvertibleError(UnitError, UnachievableError):
    """A curve asked for the input that gives an output, when it has no single one."""

    def __init__(self, kind: str, why: str) -> None:
        self.kind = kind
        super().__init__(f"{kind} curve cannot be inverted: {why}")
