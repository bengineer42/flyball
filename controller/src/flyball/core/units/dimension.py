"""Dimensions, units and prefixes.

A :class:`BaseDimension` is irreducible: the seven of the SI, plus any
pseudo-dimension worth keeping apart in composition (angle, solid angle). A
:class:`Dimension` is a product of powers of those -- every quantity has one,
and two units convert only if theirs agree. A :class:`Unit` is a magnitude on a
dimension: a factor to the coherent base, and for absolute scales (°C, °F) a
zero. Values in the framework are bare floats in a quantity's canonical unit;
this module is what converts at the edges and what a schema reads.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, NamedTuple, Self

from .errors import DimensionMismatchError


@dataclass(frozen=True, slots=True)
class Prefix:
    name: str
    symbol: str
    factor: float

    def __str__(self) -> str:
        return self.symbol


Quecto = Prefix("quecto", "q", 1e-30)
Ronto = Prefix("ronto", "r", 1e-27)
Yocto = Prefix("yocto", "y", 1e-24)
Zepto = Prefix("zepto", "z", 1e-21)
Atto = Prefix("atto", "a", 1e-18)
Femto = Prefix("femto", "f", 1e-15)
Pico = Prefix("pico", "p", 1e-12)
Nano = Prefix("nano", "n", 1e-9)
Micro = Prefix("micro", "µ", 1e-6)
Milli = Prefix("milli", "m", 1e-3)
Centi = Prefix("centi", "c", 1e-2)
Deci = Prefix("deci", "d", 1e-1)
Deca = Prefix("deca", "da", 1e1)
Hecto = Prefix("hecto", "h", 1e2)
Kilo = Prefix("kilo", "k", 1e3)
Mega = Prefix("mega", "M", 1e6)
Giga = Prefix("giga", "G", 1e9)
Tera = Prefix("tera", "T", 1e12)
Peta = Prefix("peta", "P", 1e15)
Exa = Prefix("exa", "E", 1e18)
Zetta = Prefix("zetta", "Z", 1e21)
Yotta = Prefix("yotta", "Y", 1e24)
Ronna = Prefix("ronna", "R", 1e27)
Quetta = Prefix("quetta", "Q", 1e30)


_SUPERSCRIPTS = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")


def superscript(n: int) -> str:
    return str(n).translate(_SUPERSCRIPTS)


_bases: list[BaseDimension] = []  #: registration order; a term's ``axis`` indexes here
_named: dict[tuple[Term, ...], list[NamedDimension]] = {}  #: every declared name per tuple


def _base_dimension(name: str) -> BaseDimension:
    """Unpickling target: the registered base of that name, not a copy."""
    return next(b for b in _bases if b.name == name)


class Term(NamedTuple):
    axis: int  #: index of the base dimension in ``_bases``
    exponent: int


class Dimension(tuple[Term, ...]):
    """A product of powers of base dimensions.

    Stored as ``(axis, exponent)`` terms sorted by axis with zero exponents
    dropped, so there is exactly one tuple per dimension and equality is a
    tuple compare. Build one from any iterable of pairs; duplicates merge.
    """

    __slots__ = ()

    def __new__(cls, terms: Iterable[tuple[int, int]] = ()) -> Self:
        exps: dict[int, int] = {}
        for axis, exponent in terms:
            exps[axis] = exps.get(axis, 0) + exponent
        return super().__new__(cls, (Term(a, e) for a, e in sorted(exps.items()) if e))

    # tuple's ``*`` is repetition; here it is the product of dimensions.
    def __mul__(self, other: Dimension) -> Dimension:  # type: ignore[override]  # pyright: ignore[reportIncompatibleMethodOverride]
        return Dimension((*self, *other))

    def __truediv__(self, other: Dimension) -> Dimension:
        return Dimension((*self, *(Term(a, -e) for a, e in other)))

    def __pow__(self, n: int) -> Dimension:
        return Dimension(Term(a, e * n) for a, e in self)

    def __str__(self) -> str:
        return self.formula()

    def __repr__(self) -> str:
        return f"Dimension({self!s})"

    def formula(self) -> str:
        return (
            " ".join(f"{_bases[a].symbol}{superscript(e) if e != 1 else ''}" for a, e in self)
            or "1"
        )

    @property
    def label(self) -> str:
        """The best human name: a name declared for an equal tuple, else the formula.

        Where several names share a tuple (energy and torque) the first
        declared wins, so declaration order in ``dimensions`` is the tie-break.
        """
        named = _named.get(tuple(self))
        return named[0].name if named else self.formula()

    def describe(self) -> str:
        """Label with the formula when they differ: ``Power (M L² T⁻³)``. For messages."""
        label, formula = self.label, self.formula()
        return f"{label} ({formula})" if label != formula else formula

    def unit(self, name: str, symbol: str, factor: float = 1.0, zero: float = 0.0) -> Unit:
        return Unit(name, symbol, self, factor, zero)

    def named(self, name: str, symbol: str | None = None) -> NamedDimension:
        return NamedDimension(name, self, symbol)


class NamedDimension(Dimension):
    """A dimension with the name it was declared under.

    Equality and hashing are the tuple's, so ``Torque == Energy`` still holds;
    the name is what a schema or message shows, not what conversions compare.
    Anything derived by arithmetic is a plain :class:`Dimension` again: the
    name says what this was declared as, not what any equal tuple is.
    """

    # A tuple subclass cannot add slots, so this one carries a ``__dict__``.
    _name: str
    symbol: str | None  #: the conventional quantity symbol (F, E, ρ), where one exists

    def __new__(
        cls,
        name: str,
        terms: Iterable[tuple[int, int]] = (),
        symbol: str | None = None,
    ) -> Self:
        self = super().__new__(cls, terms)
        self._name = name
        self.symbol = symbol
        _named.setdefault(tuple(self), []).append(self)
        return self

    @property
    def name(self) -> str:
        return self._name

    @property
    def label(self) -> str:
        return self._name

    def __reduce__(self) -> tuple[Any, ...]:
        # tuple pickling does not carry ``__dict__``; rebuild by name and terms.
        return (type(self), (self._name, tuple(self), self.symbol))

    def __repr__(self) -> str:
        return f"NamedDimension({self._name!r}, {self!s})"


class BaseDimension(NamedDimension):
    """An irreducible dimension: a named dimension whose one term is itself.

    Registering allocates the next axis. One object per name for the life of
    the process, so pickling gives back the registered one.
    """

    symbol: str  # pyright: ignore[reportIncompatibleVariableOverride]  a base always has one: M, L, T

    def __new__(cls, name: str, symbol: str) -> Self:  # pyright: ignore[reportIncompatibleMethodOverride]
        if any(b.name == name for b in _bases):
            raise ValueError(f"base dimension {name!r} is already registered")
        self = super().__new__(cls, name, [(len(_bases), 1)], symbol)
        _bases.append(self)
        return self

    def __reduce__(self) -> tuple[Any, ...]:
        return (_base_dimension, (self.name,))

    @property
    def axis(self) -> int:
        return self[0].axis


DIMENSIONLESS = NamedDimension("Dimensionless")


@dataclass(frozen=True, slots=True)
class Unit:
    """A magnitude on a dimension.

    ``factor`` takes a value to the dimension's coherent base unit; ``zero`` is
    the base-unit value at this unit's 0, non-zero only for absolute scales
    (°C, °F). Intervals, rates and anything composed ignore ``zero``: there is
    no such thing as an absolute J/°C, and a rise of 5 °C is a rise of 5 K.
    Which conversion a value gets is decided by its quantity, not here.
    """

    name: str
    symbol: str
    dimension: Dimension
    factor: float = 1.0
    zero: float = 0.0
    prefix: Prefix | None = None

    def to(self, other: Unit, value: float) -> float:
        """Convert an interval: differences, rates, composed units."""
        self._check(other)
        return value * self.factor / other.factor

    def to_absolute(self, other: Unit, value: float) -> float:
        """Convert a point on a scale, through the base so zeros are honoured."""
        self._check(other)
        return (value * self.factor + self.zero - other.zero) / other.factor

    def prefixed(self, prefix: Prefix) -> Unit:
        """This unit with an SI prefix. One prefix per symbol: mkg is not a unit."""
        if self.prefix is not None:
            raise ValueError(f"{self} already carries a prefix")
        return Unit(
            prefix.name + self.name,
            prefix.symbol + self.symbol,
            self.dimension,
            self.factor * prefix.factor,
            self.zero,
            prefix,
        )

    def __mul__(self, other: Unit) -> Unit:
        return Unit(
            f"{self.name} {other.name}",
            f"{self.symbol}·{other.symbol}",
            self.dimension * other.dimension,
            self.factor * other.factor,
        )

    def __truediv__(self, other: Unit) -> Unit:
        return Unit(
            f"{self.name} per {other.name}",
            f"{self.symbol}/{other.symbol}",
            self.dimension / other.dimension,
            self.factor / other.factor,
        )

    def __pow__(self, n: int) -> Unit:
        return Unit(
            f"{self.name}{superscript(n)}",
            f"{self.symbol}{superscript(n)}",
            self.dimension**n,
            self.factor**n,
        )

    def __str__(self) -> str:
        return self.symbol

    def _check(self, other: Unit) -> None:
        if self.dimension != other.dimension:
            raise DimensionMismatchError(self, other)
