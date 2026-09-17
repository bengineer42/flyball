"""Dimensions, units and prefixes.

A [BaseDimension][flyball.core.units.dimension.BaseDimension] is irreducible:
the seven of the SI, plus angle and solid angle. A
[Dimension][flyball.core.units.dimension.Dimension] is a product of their
powers; two units convert only if theirs agree. A
[Unit][flyball.core.units.dimension.Unit] is a magnitude on a dimension: a
factor to the coherent base, and for absolute scales a zero. Values in the
framework are bare floats in a canonical unit; conversion happens at the edges.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple, Self

from .errors import DimensionMismatchError, UnitNotFoundError

if TYPE_CHECKING:
    from flyball.core.quantity import Quantity


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

_prefixes: tuple[Prefix, ...] = tuple(
    sorted(
        (p for p in list(globals().values()) if isinstance(p, Prefix)),
        key=lambda p: -len(p.symbol),
    )
)


_SUPERSCRIPTS = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")
_FROM_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
_SUPERSCRIPT_DIGITS = set("⁰¹²³⁴⁵⁶⁷⁸⁹")
_units: dict[str, Unit] = {}  # every unit by symbol, first definition wins


def superscript(n: int) -> str:
    return str(n).translate(_SUPERSCRIPTS)


_bases: list[BaseDimension] = []  # registration order; a term's `axis` indexes here
_named: dict[tuple[Term, ...], list[NamedDimension]] = {}  # every declared name per tuple


def _base_dimension(name: str) -> BaseDimension:
    """Unpickling target: the registered base of that name, not a copy."""
    return next(b for b in _bases if b.name == name)


class Term(NamedTuple):
    axis: int  # index of the base dimension in `_bases`
    exponent: int


class Dimension(tuple[Term, ...]):
    """A product of powers of base dimensions.

    Stored as `(axis, exponent)` terms sorted by axis, zero exponents dropped,
    so equality is a tuple compare. Build from any iterable of pairs.
    """

    __slots__ = ()

    def __new__(cls, terms: Iterable[tuple[int, int]] = ()) -> Self:
        exps: dict[int, int] = {}
        for axis, exponent in terms:
            exps[axis] = exps.get(axis, 0) + exponent
        return super().__new__(cls, (Term(a, e) for a, e in sorted(exps.items()) if e))

    # tuple's `*` is repetition; here it is the product of dimensions.
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
        """A name declared for an equal tuple (first declared wins), else the formula."""
        named = _named.get(tuple(self))
        return named[0].name if named else self.formula()

    def describe(self) -> str:
        """Label with the formula when they differ: `Power (M L² T⁻³)`. For messages."""
        label, formula = self.label, self.formula()
        return f"{label} ({formula})" if label != formula else formula

    def unit(
        self,
        name: str,
        symbol: str,
        factor: float = 1.0,
        zero: float = 0.0,
        scale: tuple[float, float] | None = None,
    ) -> Unit:
        return Unit(name, symbol, self, factor, zero, scale=scale)

    def named(self, name: str, symbol: str | None = None) -> NamedDimension:
        return NamedDimension(name, self, symbol)


class NamedDimension(Dimension):
    """A dimension with the name it was declared under.

    Equality is the tuple's, so `Torque == Energy`; the name is for display.
    Arithmetic yields a plain [Dimension][flyball.core.units.dimension.Dimension].
    """

    # A tuple subclass cannot add slots, so this one carries a `__dict__`.
    _name: str
    symbol: str | None  # the conventional quantity symbol (F, E, ρ), where one exists

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
        # tuple pickling does not carry `__dict__`; rebuild by name and terms.
        return (type(self), (self._name, tuple(self), self.symbol))

    def __repr__(self) -> str:
        return f"NamedDimension({self._name!r}, {self!s})"


class BaseDimension(NamedDimension):
    """An irreducible dimension: a named dimension whose one term is itself.

    Registering allocates the next axis. One object per name per process;
    pickling gives back the registered one.
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

    `factor` takes a value to the coherent base unit; `zero` is the base-unit
    value at this unit's 0, non-zero only for absolute scales (°C, °F).
    Intervals and composed units ignore `zero`: a rise of 5 °C is 5 K. The
    quantity decides which conversion applies. `scale` is the full scale a
    UI shows by default, in this unit, when a signal says nothing of its
    own: 0-1 for `One`, 0-100 for `Percent`, 0-360 for `Degree`; None for a
    unit with no natural one (metres). A default for display, never a bound.
    """

    name: str
    symbol: str
    dimension: Dimension
    factor: float = 1.0
    zero: float = 0.0
    prefix: Prefix | None = None
    scale: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        # First definition of a symbol wins: a composed `L/min` built twice is
        # the same unit, and a clash of different units is a naming mistake to
        # surface, not silently shadow.
        existing = _units.get(self.symbol)
        if existing is None:
            _units[self.symbol] = self
        elif (existing.dimension, existing.factor, existing.zero) != (
            self.dimension,
            self.factor,
            self.zero,
        ):
            raise ValueError(f"symbol {self.symbol!r} is already {existing.name}")

    @classmethod
    def get(cls, symbol: str) -> Unit:
        """The unit written `symbol`: exact, prefixed (`kPa`), or a quotient or product (`g/m³`).

        Raises:
            UnitNotFoundError: If nothing matches.
        """
        from . import si  # ruff: ignore[unused-import]  populates the registry with the SI units

        if (unit := _units.get(symbol)) is not None:
            return unit
        if "/" in symbol:
            num, _, den = symbol.partition("/")
            return cls.get(num) / cls.get(den)
        if "·" in symbol:
            a, _, b = symbol.partition("·")
            return cls.get(a) * cls.get(b)
        if symbol[-1:] in _SUPERSCRIPT_DIGITS:  # m³, s⁻²
            root, power = symbol.rstrip("⁰¹²³⁴⁵⁶⁷⁸⁹⁻"), symbol[len(symbol.rstrip("⁰¹²³⁴⁵⁶⁷⁸⁹⁻")) :]
            return cls.get(root) ** int(power.translate(_FROM_SUPERSCRIPT))
        for prefix in _prefixes:
            if prefix.symbol and symbol.startswith(prefix.symbol):
                root = _units.get(symbol[len(prefix.symbol) :])
                if root is not None and root.prefix is None:
                    return root.prefixed(prefix)
        raise UnitNotFoundError(symbol)

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

    def quantity(self, name: str | None = None) -> Quantity:
        """A [Quantity][flyball.core.quantity.Quantity] in this unit.

        `name` defaults to the dimension's name, lower-cased: `Celsius.quantity()`
        is `Quantity("temperature", Celsius)`. A unit on a dimension nobody has
        named (a composed `W/m²` before `Irradiance` was declared) has no
        default, so the name must be given.

        Raises:
            ValueError: No name given and the dimension has none.
        """
        from flyball.core.quantity import Quantity

        if name is None:
            label, formula = self.dimension.label, self.dimension.formula()
            if label == formula:
                raise ValueError(
                    f"{self.symbol} is on an unnamed dimension ({formula}): name the quantity"
                )
            name = label.lower()
        return Quantity(name, self)

    def __str__(self) -> str:
        return self.symbol

    def _check(self, other: Unit) -> None:
        if self.dimension != other.dimension:
            raise DimensionMismatchError(self, other)
