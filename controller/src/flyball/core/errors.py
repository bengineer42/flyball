"""Domain errors, grouped by the answer they give a caller.

The four bases below carry the whole classification: what a caller (or an HTTP
client) can do about a failure, rather than which subsystem raised it. Each also
mixes in the builtin a library consumer would reach for, so ``except LookupError``
and ``except RuntimeError`` behave as expected without importing anything here.
"""

from __future__ import annotations


class FlyballError(Exception):
    """Base for everything flyball raises."""


class NotFoundError(FlyballError, LookupError):
    """No such named thing. Nothing the caller does to the rig will conjure it."""


class ConflictError(FlyballError, RuntimeError):
    """The rig is in the wrong state, and the caller can put it right."""


class NotReadyError(FlyballError):
    """The rig is not configured, or the value does not exist yet.

    No builtin fits: unlike :class:`ConflictError` the caller cannot fix this by
    issuing another request, and unlike :class:`HardwareError` nothing is broken.
    """


class UnachievableError(FlyballError, ValueError):
    """Well formed, but the numbers cannot be applied to this rig.

    Sorted ahead of :class:`FlyballError` in the MRO of anything that inherits
    it, so a subsystem base such as ``PumpError`` cannot shadow the mapping.
    """


class HardwareError(FlyballError, OSError):
    """A device failed. Usually transient, and never the caller's fault."""


# region Not configured


class RecorderNotSetError(NotReadyError):
    def __init__(self) -> None:
        super().__init__(
            "Recorder not set. Use set_recorder() to set a recorder before starting recording."
        )


class ReadersNotSetError(NotReadyError):
    def __init__(self) -> None:
        super().__init__(
            "Readers not set. Use set_readers() to set readers before reading sensor data."
        )


# endregion
