"""A QCoDeS instrument's parameters as a flyball device.

Nothing here imports qcodes at module load: validating a `QCoDeSConfig` does, so the
`qcodes` extra is only needed where it is actually used. A fake with the
same attributes drives the tests.
"""

from ._qcodes import QCoDeS, QCoDeSConfig, QCoDeSSignal, bounds, unit_for

__all__ = ["QCoDeS", "QCoDeSConfig", "QCoDeSSignal", "bounds", "unit_for"]
