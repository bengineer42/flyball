"""Recording and reading back.

`Store` is the interface; `SqliteStore` the one implementation. A rig opens
a session and holds its `SessionWriter`; a server holds a `Store` and asks
it for series, ticks, events and spans to draw.
"""

from .errors import (
    NotDeclaredError,
    SchemaError,
    SessionEndedError,
    SessionNotFoundError,
    StoreError,
    TuningNotFoundError,
)
from .sqlite import SqliteSessionWriter, SqliteStore
from .store import SessionWriter, Store
from .types import (
    ActuatorRow,
    ChannelRow,
    Downsample,
    Event,
    LoopRow,
    MeasurandRow,
    Point,
    ProgramFormat,
    ProgramRow,
    Series,
    SessionRow,
    SourceRow,
    Span,
    SpanKind,
    Tick,
    TuningRow,
    Window,
)

__all__ = [
    "ActuatorRow",
    "ChannelRow",
    "Downsample",
    "Event",
    "LoopRow",
    "MeasurandRow",
    "NotDeclaredError",
    "Point",
    "ProgramFormat",
    "ProgramRow",
    "SchemaError",
    "Series",
    "SessionEndedError",
    "SessionNotFoundError",
    "SessionRow",
    "SessionWriter",
    "SourceRow",
    "Span",
    "SpanKind",
    "SqliteSessionWriter",
    "SqliteStore",
    "Store",
    "StoreError",
    "Tick",
    "TuningNotFoundError",
    "TuningRow",
    "Window",
]
