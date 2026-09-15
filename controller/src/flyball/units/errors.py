"""Errors the units package raises.

``flyball.core`` builds on this package, so these cannot inherit the bases in
:mod:`flyball.core.errors` without a cycle. They are plain ``ValueError`` subclasses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dimension import Unit


class UnitError(ValueError):
    """Base for everything flyball.units raises."""


class DimensionMismatchError(UnitError):
    """A conversion between units of different dimensions.

    Well formed -- both units exist -- but no factor relates a length to a
    time.
    """

    def __init__(self, source: Unit, target: Unit) -> None:
        self.source = source
        self.target = target
        super().__init__(
            f"cannot convert {source} to {target}: "
            f"{source.dimension.describe()} vs {target.dimension.describe()}"
        )
