from typing import TYPE_CHECKING

from humctrl.cmds import Command
from humctrl.runners import Runner

if TYPE_CHECKING:
    from humctrl.controller import ControlLaw, ControlLawConfig, Controller
    from humctrl.readers import ReaderSource


class HumCtrlError(Exception): ...


class RecorderNotSetError(HumCtrlError):
    def __init__(self) -> None:
        super().__init__(
            "Recorder not set. Use set_recorder() to set a recorder before starting recording."
        )


class ManagerNotRunningError(HumCtrlError):
    def __init__(self) -> None:
        super().__init__(
            "Manager not running. Use start() to start the manager before calling this method."
        )


class ControlLawNotSetError(HumCtrlError):
    def __init__(self) -> None:
        super().__init__(
            "Control law not set. Use set_control_law() to set a control law before starting "
            "regulation."
        )


class ReadersNotSetError(HumCtrlError):
    def __init__(self) -> None:
        super().__init__(
            "Readers not set. Use set_readers() to set readers before reading sensor data."
        )


class ProcessSensorNotSetError(HumCtrlError):
    def __init__(self) -> None:
        super().__init__(
            "Process sensor not set. Use set_process_sensor() to set a process sensor before "
            "reading process data."
        )


class TargetHumidityNotSetError(HumCtrlError):
    def __init__(self) -> None:
        super().__init__(
            "Target humidity not set. Use start_regulating() to set a target humidity before "
            "reading regulated humidity."
        )


class TargetStreamNotSetError(HumCtrlError):
    def __init__(self) -> None:
        super().__init__(
            "Target stream not set. Use start_stream() to set a target stream before "
            "reading regulated humidity."
        )


class ControllerNotRunningError(HumCtrlError):
    def __init__(self) -> None:
        super().__init__(
            "Controller not running. Use start_controller() to start the controller before calling "
            "this method."
        )


class CurrentWetFractionNotSetError(HumCtrlError):
    def __init__(self) -> None:
        super().__init__(
            "Current wet fraction not set. Use set_flows() or set_blend() to set the current wet "
            "fraction before calling this method."
        )


class PumpsNotSetError(HumCtrlError):
    def __init__(self) -> None:
        super().__init__("Pumps not set. Use set_pumps() to set pumps before using them.")


class ProcessReadingNotAvailableError(HumCtrlError):
    def __init__(self) -> None:
        super().__init__(
            "Process reading not available. Use read_process() to read process data before "
            "accessing it."
        )


class PumpHumidityNotSetError(HumCtrlError):
    def __init__(self, line: str) -> None:
        super().__init__(f"Pump humidities for '{line}' pump not set.")


class ControllerAlreadyRunningError(HumCtrlError):
    def __init__(
        self, current: Controller, new: ControlLaw | ControlLawConfig | Controller | str
    ) -> None:
        super().__init__(
            f"Controller {current.type} is already running. Stop it before starting a "
            f"{new if isinstance(new, str) else new.type}."
        )


class CommandAlreadyRunningError(HumCtrlError):
    def __init__(self, current: Runner, new: Command) -> None:
        super().__init__(
            f"Command is already running ({current}). Interrupt it before starting a new one "
            f"({new})."
        )


class ProgramAlreadyRunningError(HumCtrlError):
    def __init__(self) -> None:
        super().__init__("Program is already running. Interrupt it before starting a new ones")


class ReaderError(HumCtrlError):
    def __init__(self, reader: ReaderSource, error: Exception) -> None:
        super().__init__(f"Error reading from {reader}: {error}")
