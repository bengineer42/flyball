from .dimensions import Temperature

Rankine = Temperature.unit("rankine", "°R", 5 / 9)
Fahrenheit = Temperature.unit("fahrenheit", "°F", 5 / 9, zero=459.67 * 5 / 9)
