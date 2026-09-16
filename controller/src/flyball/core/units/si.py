"""SI units: the base units, the named derived units, and the accepted non-SI ones."""

from math import pi as PI

from .dimension import DIMENSIONLESS, Kilo
from .dimensions import (
    AmountOfSubstance,
    Angle,
    Capacitance,
    CatalyticActivity,
    ElectricalConductance,
    ElectricalResistance,
    ElectricCharge,
    ElectricCurrent,
    ElectricPotential,
    Energy,
    Force,
    Frequency,
    Illuminance,
    Inductance,
    Length,
    LuminousFlux,
    LuminousIntensity,
    MagneticFlux,
    MagneticFluxDensity,
    Mass,
    Power,
    Pressure,
    SolidAngle,
    Temperature,
    Time,
    Volume,
)

# The kilogram is the coherent unit of mass but the gram is what prefixes
# attach to, so the gram carries the factor and the kilogram is derived.
Gram = Mass.unit("gram", "g", 1e-3)
Kilogram = Gram.prefixed(Kilo)
Metre = Length.unit("metre", "m")
Second = Time.unit("second", "s")
Kelvin = Temperature.unit("kelvin", "K")
Ampere = ElectricCurrent.unit("ampere", "A")
Mole = AmountOfSubstance.unit("mole", "mol")
Candela = LuminousIntensity.unit("candela", "cd")
Radian = Angle.unit("radian", "rad")
Steradian = SolidAngle.unit("steradian", "sr")

Hertz = Frequency.unit("hertz", "Hz")
Newton = Force.unit("newton", "N")
Pascal = Pressure.unit("pascal", "Pa")
Joule = Energy.unit("joule", "J")
Watt = Power.unit("watt", "W")
Coulomb = ElectricCharge.unit("coulomb", "C")
Volt = ElectricPotential.unit("volt", "V")
Ohm = ElectricalResistance.unit("ohm", "Ω")
Siemens = ElectricalConductance.unit("siemens", "S")
Farad = Capacitance.unit("farad", "F")
Henry = Inductance.unit("henry", "H")
Tesla = MagneticFluxDensity.unit("tesla", "T")
Weber = MagneticFlux.unit("weber", "Wb")
Lumen = LuminousFlux.unit("lumen", "lm")
Lux = Illuminance.unit("lux", "lx")
Katal = CatalyticActivity.unit("katal", "kat")

# Absolute scales: same interval as the kelvin, different zero.
Celsius = Temperature.unit("celsius", "°C", zero=273.15)

Unitless = DIMENSIONLESS.unit("", "")

# The unit of a count, a ratio, a status word, a duty: what "no unit" means in a table.
One = DIMENSIONLESS.unit("one", "1")
Percent = DIMENSIONLESS.unit("percent", "%", 0.01)


# Non-SI units accepted for use with the SI.
Minute = Time.unit("minute", "min", 60)
Hour = Time.unit("hour", "h", 3600)
Day = Time.unit("day", "d", 86400)

Degree = Angle.unit("degree", "°", PI / 180)
DegreeMinute = Angle.unit("degree minute", "′", PI / 10800)  # ruff: ignore[ambiguous-unicode-character-string]
DegreeSecond = Angle.unit("degree second", "″", PI / 648000)

Litre = Volume.unit("litre", "L", 1e-3)
Tonne = Mass.unit("tonne", "t", 1e3)
Dalton = Mass.unit("dalton", "Da", 1.6605390689252e-27)
ElectronVolt = Energy.unit("electron volt", "eV", 1.602176634e-19)
