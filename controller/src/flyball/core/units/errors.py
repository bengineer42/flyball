"""Errors the units package raises, classified as in :mod:`flyball.core.errors`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from flyball.core.errors import FlyballError, UnachievableError

if TYPE_CHECKING:
    from .dimension import Unit


class UnitError(FlyballError):
    """Base for everything flyball.core.units raises."""


class DimensionMismatchError(UnitError, UnachievableError):
    """A conversion between units of different dimensions.

    Well formed -- both units exist -- but no factor relates a length to a
    time, so it is unachievable rather than malformed.
    """

    def __init__(self, source: Unit, target: Unit) -> None:
        self.source = source
        self.target = target
        super().__init__(
            f"cannot convert {source} to {target}: "
            f"{source.dimension.describe()} vs {target.dimension.describe()}"
        )
