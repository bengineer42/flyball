"""Domain errors, classified by what a caller can do about them, not by which subsystem raised them.

Each base mixes in the matching builtin, so `except LookupError` works
without importing anything here.
"""

from __future__ import annotations


class FlyballError(Exception):
    """Base for everything flyball raises."""


class NotFoundError(FlyballError, LookupError):
    """No such named thing. Nothing the caller does to the rig will conjure it."""


class ConflictError(FlyballError, RuntimeError):
    """The rig is in the wrong state, and the caller can put it right."""


class NotReadyError(FlyballError):
    """The rig is not configured, or the value does not exist yet. Nothing is broken."""


class UnachievableError(FlyballError, ValueError):
    """Well formed, but the numbers cannot be applied to this rig.

    Precedes [FlyballError][flyball.foundation.errors.FlyballError] in the MRO so a
    subsystem base cannot shadow the mapping.
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
