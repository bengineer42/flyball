from pathlib import Path

from flyball.foundation.errors import ConflictError, FlyballError, HardwareError, NotFoundError


class StoreError(FlyballError):
    """Base for everything flyball.record raises."""


class SessionNotFoundError(StoreError, NotFoundError):
    def __init__(self, session_id: int) -> None:
        super().__init__(f"Session {session_id} not found")


class TuningNotFoundError(StoreError, NotFoundError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Tuning {name!r} not found")


class ProgramNotFoundError(StoreError, NotFoundError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Program {name!r} not found")


class DashboardNotFoundError(StoreError, NotFoundError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Dashboard {name!r} not found")


class SessionEndedError(StoreError, ConflictError):
    def __init__(self, session_id: int) -> None:
        super().__init__(f"Session {session_id} has ended; nothing more can be written to it")


class NotDeclaredError(StoreError, ConflictError):
    """A row named a device, signal or controller the session never declared."""

    def __init__(self, kind: str, name: str) -> None:
        super().__init__(f"{kind} {name!r} was not declared in this session")


class SchemaError(StoreError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class StoreUnavailableError(StoreError, HardwareError):
    """The store could not be reached: locked by another writer, out of disk, unreadable.

    Usually transient, and never the caller's fault; retry. The same sqlite error
    also covers a transaction opened inside another, which is a bug, so sqlite's own
    message is kept in the text and the original rides as `__cause__`.
    """

    def __init__(self, message: str, path: str | Path) -> None:
        super().__init__(message)
        self.path = path


class ConstraintError(StoreError, ConflictError):
    """The store refused a write: it names a row that does not exist, or repeats a unique one.

    Retrying the same write fails the same way; change what is written.
    """

    def __init__(self, message: str, path: str | Path) -> None:
        super().__init__(message)
        self.path = path
