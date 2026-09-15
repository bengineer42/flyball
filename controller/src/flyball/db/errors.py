from flyball.core.errors import ConflictError, FlyballError, NotFoundError


class StoreError(FlyballError):
    """Base for everything flyball.db raises."""


class SessionNotFoundError(StoreError, NotFoundError):
    def __init__(self, session_id: int) -> None:
        super().__init__(f"Session {session_id} not found")


class TuningNotFoundError(StoreError, NotFoundError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Tuning {name!r} not found")


class SessionEndedError(StoreError, ConflictError):
    def __init__(self, session_id: int) -> None:
        super().__init__(f"Session {session_id} has ended; nothing more can be written to it")


class NotDeclaredError(StoreError, ConflictError):
    """A row named a source, measurand or loop the session never declared."""

    def __init__(self, kind: str, name: str) -> None:
        super().__init__(f"{kind} {name!r} was not declared in this session")


class SchemaError(StoreError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
