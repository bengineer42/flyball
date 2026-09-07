"""Domain errors, grouped by the answer they give a caller.

The four bases below carry the whole classification: what a caller (or an HTTP
client) can do about a failure, rather than which subsystem raised it. Each also
mixes in the builtin a library consumer would reach for, so ``except LookupError``
and ``except RuntimeError`` behave as expected without importing anything here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from humctrl.cmds import Command
    from humctrl.controller import ControlLaw, ControlLawConfig, DualPumpController
    from humctrl.controller.types import Tuning
    from humctrl.readers import ReaderSource
    from humctrl.runners import Runner


class HumCtrlError(Exception):
    """Base for everything humctrl raises."""


class NotFoundError(HumCtrlError, LookupError):
    """No such named thing. Nothing the caller does to the rig will conjure it."""


class ConflictError(HumCtrlError, RuntimeError):
    """The rig is in the wrong state, and the caller can put it right."""


class NotReadyError(HumCtrlError):
    """The rig is not configured, or the value does not exist yet.

    No builtin fits: unlike :class:`ConflictError` the caller cannot fix this by
    issuing another request, and unlike :class:`HardwareError` nothing is broken.
    """


class UnachievableError(HumCtrlError, ValueError):
    """Well formed, but the numbers cannot be applied to this rig.

    Sorted ahead of :class:`HumCtrlError` in the MRO of anything that inherits
    it, so a subsystem base such as ``PumpError`` cannot shadow the mapping.
    """


class HardwareError(HumCtrlError, OSError):
    """A device failed. Usually transient, and never the caller's fault."""


# region Not configured


class RecorderNotSetError(NotReadyError):
    def __init__(self) -> None:
        super().__init__(
            "Recorder not set. Use set_recorder() to set a recorder before starting recording."
        )


class ControlLawNotSetError(NotReadyError):
    def __init__(self) -> None:
        super().__init__(
            "Control law not set. Use set_control_law() to set a control law before starting "
            "regulation."
        )


class ReadersNotSetError(NotReadyError):
    def __init__(self) -> None:
        super().__init__(
            "Readers not set. Use set_readers() to set readers before reading sensor data."
        )


class PumpsNotSetError(NotReadyError):
    def __init__(self) -> None:
        super().__init__("Pumps not set. Use set_pumps() to set pumps before using them.")


class PumpHumidityNotSetError(NotReadyError):
    def __init__(self, line: str) -> None:
        super().__init__(f"Pump humidities for {line!r} pump not set.")


class ProcessReadingNotAvailableError(NotReadyError):
    """The sensor is fitted but has not been read yet. The next loop tick fixes it."""

    def __init__(self) -> None:
        super().__init__(
            "Process reading not available. Use read_process() to read process data before "
            "accessing it."
        )


# endregion

# region Wrong state


class ManagerNotRunningError(ConflictError):
    def __init__(self) -> None:
        super().__init__(
            "Manager not running. Use start() to start the manager before calling this method."
        )


class ControllerNotRunningError(ConflictError):
    def __init__(self) -> None:
        super().__init__(
            "Controller not running. Use start_controller() to start the controller before calling "
            "this method."
        )


class TargetHumidityNotSetError(ConflictError):
    def __init__(self) -> None:
        super().__init__(
            "Target humidity not set. Use start_regulating() to set a target humidity before "
            "reading regulated humidity."
        )


class TargetStreamNotSetError(ConflictError):
    def __init__(self) -> None:
        super().__init__(
            "Target stream not set. Use start_stream() to set a target stream before "
            "reading regulated humidity."
        )


class CurrentWetFractionNotSetError(ConflictError):
    def __init__(self) -> None:
        super().__init__(
            "Current wet fraction not set. Use set_flows() or set_blend() to set the current wet "
            "fraction before calling this method."
        )


class ControllerAlreadyRunningError(ConflictError):
    def __init__(
        self,
        current: DualPumpController | str,
        new: ControlLaw | ControlLawConfig | DualPumpController | Tuning | str,
    ) -> None:
        super().__init__(
            f"Controller {current if isinstance(current, str) else current.tag} is already "
            f"running. Stop it before starting a {new if isinstance(new, str) else new.tag}."
        )


class CommandAlreadyRunningError(ConflictError):
    def __init__(self, current: Runner, new: Command) -> None:
        super().__init__(
            f"Command is already running ({current}). Interrupt it before starting a new one "
            f"({new})."
        )


class ProgramAlreadyRunningError(ConflictError):
    def __init__(self) -> None:
        super().__init__("Program is already running. Interrupt it before starting a new one.")


# endregion

# region Not found


class TuningNotRegisteredError(NotFoundError):
    def __init__(self, tuning: str) -> None:
        super().__init__(f"Control law tuning with name {tuning!r} is not registered.")


# endregion

# region Hardware


class ReaderError(HardwareError):
    def __init__(self, reader: ReaderSource, error: Exception) -> None:
        super().__init__(f"Error reading from {reader}: {error}")


# endregion
