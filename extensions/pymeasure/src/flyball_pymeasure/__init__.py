"""A PyMeasure instrument's measurement/control/setting properties as a flyball device.

Nothing here imports pymeasure at module load: validating a `PyMeasureConfig` does,
so the `pymeasure` extra is only needed where it is actually used.
"""

from ._pymeasure import PyMeasure, PyMeasureConfig, PyMeasureSignal, properties, unit_from_doc

__all__ = [
    "PyMeasure",
    "PyMeasureConfig",
    "PyMeasureSignal",
    "properties",
    "unit_from_doc",
]
