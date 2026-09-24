"""Units outside the SI, and outside the SI's accepted list, that rigs still read in."""

from .dimension import BaseDimension
from .dimensions import Frequency, Length, Pressure, Temperature, VolumeFlow

Rankine = Temperature.unit("rankine", "°R", 5 / 9)
Fahrenheit = Temperature.unit("fahrenheit", "°F", 5 / 9, zero=459.67 * 5 / 9)

# Vacuum and gas handling: bar takes prefixes (`mbar`); the torr is 101325/760 Pa.
Bar = Pressure.unit("bar", "bar", 1e5)
Torr = Pressure.unit("torr", "Torr", 101325 / 760)
Angstrom = Length.unit("ångström", "Å", 1e-10)

# A rotation rate as a tachometer counts it: turns per minute, a frequency.
RevolutionsPerMinute = Frequency.unit("revolutions per minute", "rpm", 1 / 60)

# Mass-flow controllers quote volume flow at standard conditions; the unit is
# the volume, the standard conditions are the instrument's, not converted here.
StandardCubicCentimetrePerMinute = VolumeFlow.unit(
    "standard cubic centimetre per minute", "sccm", 1e-6 / 60
)
StandardLitrePerMinute = VolumeFlow.unit("standard litre per minute", "slm", 1e-3 / 60)

# Turbidity as a nephelometer reports it: light scattered by a sample, against a
# formazin standard. It converts to nothing else, so it is a dimension of its own.
Turbidity = BaseDimension("Turbidity", "Tb")
NephelometricTurbidityUnit = Turbidity.unit("nephelometric turbidity unit", "NTU")
