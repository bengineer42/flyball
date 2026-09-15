"""The base dimensions and the derived dimensions with SI names."""

from .dimension import DIMENSIONLESS, BaseDimension

# Registration order is display order: M L T Θ I N J, then the pseudo-dimensions.
Mass = BaseDimension("Mass", "M")
Length = BaseDimension("Length", "L")
Time = BaseDimension("Time", "T")
Temperature = BaseDimension("Temperature", "Θ")
ElectricCurrent = BaseDimension("Electric Current", "I")
AmountOfSubstance = BaseDimension("Amount of Substance", "N")
LuminousIntensity = BaseDimension("Luminous Intensity", "J")

# Dimensionless in the SI, kept apart so rad/s and Hz, lm and cd, do not
# compare equal.
Angle = BaseDimension("Angle", "∠")
SolidAngle = BaseDimension("Solid Angle", "Ω")


Frequency = (DIMENSIONLESS / Time).named("Frequency", "f")

Area = (Length**2).named("Area", "A")
Volume = (Length**3).named("Volume", "V")

Speed = (Length / Time).named("Speed", "v")
Acceleration = (Speed / Time).named("Acceleration", "a")
Jerk = (Acceleration / Time).named("Jerk", "j")
Snap = (Jerk / Time).named("Snap")
Crackle = (Snap / Time).named("Crackle")
Pop = (Crackle / Time).named("Pop")

AngularVelocity = (Angle / Time).named("Angular velocity", "ω")
AngularAcceleration = (AngularVelocity / Time).named("Angular acceleration", "α")
AngularJerk = (AngularAcceleration / Time).named("Angular jerk")
AngularSnap = (AngularJerk / Time).named("Angular snap")
AngularCrackle = (AngularSnap / Time).named("Angular crackle")
AngularPop = (AngularCrackle / Time).named("Angular pop")

Force = (Mass * Acceleration).named("Force", "F")
Energy = (Force * Length).named("Energy", "E")
Power = (Energy / Time).named("Power", "P")
Pressure = (Force / Area).named("Pressure", "p")

ElectricCharge = (Time * ElectricCurrent).named("Electric charge", "Q")
ElectricPotential = (Power / ElectricCurrent).named("Electric potential", "U")
ElectricalResistance = (ElectricPotential / ElectricCurrent).named("Electrical resistance", "R")
ElectricalConductance = (ElectricCurrent / ElectricPotential).named("Electrical conductance", "G")
Capacitance = (ElectricCharge / ElectricPotential).named("Capacitance", "C")
MagneticFlux = (ElectricPotential * Time).named("Magnetic flux", "Φ")
MagneticFluxDensity = (MagneticFlux / Area).named("Magnetic flux density", "B")
Inductance = (MagneticFlux / ElectricCurrent).named("Inductance", "L")

# lm = cd·sr, lx = lm/m²: the steradian has to be carried once solid angle is a
# dimension of its own.
LuminousFlux = (LuminousIntensity * SolidAngle).named("Luminous flux", "Φv")
Illuminance = (LuminousFlux / Area).named("Illuminance", "Ev")

CatalyticActivity = (AmountOfSubstance / Time).named("Catalytic activity", "z")

# Several of these share a dimension with another (density and mass
# concentration, energy and torque, frequency and radioactivity, specific energy
# and absorbed dose). They are equal tuples; telling them apart is the
# quantity's job, not the dimension's.
MassFlow = (Mass / Time).named("Mass flow", "qm")
VolumeFlow = (Volume / Time).named("Volume flow", "qV")
Density = (Mass / Volume).named("Density", "ρ")
MassConcentration = (Mass / Volume).named("Mass concentration", "ρ")
AmountConcentration = (AmountOfSubstance / Volume).named("Amount concentration", "c")
SpecificEnergy = (Energy / Mass).named("Specific energy", "e")
SpecificHeatCapacity = (Energy / (Mass * Temperature)).named("Specific heat capacity", "c")
HeatCapacity = (Energy / Temperature).named("Heat capacity", "C")
ThermalConductivity = (Power / (Length * Temperature)).named("Thermal conductivity", "λ")
DynamicViscosity = (Pressure * Time).named("Dynamic viscosity", "η")
KinematicViscosity = (Area / Time).named("Kinematic viscosity", "ν")
Momentum = (Mass * Speed).named("Momentum", "p")
Torque = (Force * Length).named("Torque", "M")

Wavenumber = (DIMENSIONLESS / Length).named("Wavenumber", "σ")
SurfaceTension = (Force / Length).named("Surface tension", "γ")
EnergyDensity = (Energy / Volume).named("Energy density", "w")
Irradiance = (Power / Area).named("Irradiance", "E")
HeatFlux = (Power / Area).named("Heat flux", "q")
ElectricFieldStrength = (ElectricPotential / Length).named("Electric field strength", "E")
ChargeDensity = (ElectricCharge / Volume).named("Charge density", "ρ")
CurrentDensity = (ElectricCurrent / Area).named("Current density", "J")
Permittivity = (Capacitance / Length).named("Permittivity", "ε")
Permeability = (Inductance / Length).named("Permeability", "μ")
Resistivity = (ElectricalResistance * Length).named("Resistivity", "ρ")
Luminance = (LuminousIntensity / Area).named("Luminance", "Lv")
Radioactivity = (DIMENSIONLESS / Time).named("Radioactivity", "A")
AbsorbedDose = (Energy / Mass).named("Absorbed dose", "D")
DoseEquivalent = (Energy / Mass).named("Dose equivalent", "H")
Exposure = (ElectricCharge / Mass).named("Exposure", "X")
