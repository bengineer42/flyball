"""Errors the units package raises, classified as in [flyball.core.errors][flyball.core.errors]."""

from __future__ import annotations

from typing import TYPE_CHECKING

from flyball.core.errors import FlyballError, NotFoundError, UnachievableError

if TYPE_CHECKING:
    from .dimension import Unit


class UnitError(FlyballError):
    """Base for everything flyball.core.units raises."""


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
