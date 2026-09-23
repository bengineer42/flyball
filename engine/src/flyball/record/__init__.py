"""Recording and reading back.

`Store` is the interface; `SqliteStore` the one implementation. A rig opens
a session and holds its `SessionWriter`; a server holds a `Store` and asks
it for series, write states, ticks, events and spans to draw.
"""

from .errors import (
    ConstraintError,
    NotDeclaredError,
    SchemaError,
    SessionEndedError,
    SessionNotFoundError,
    StoreError,
    StoreUnavailableError,
    TuningNotFoundError,
)
from .sqlite import SqliteSessionWriter, SqliteStore
from .store import SessionWriter, Store
from .types import (
    ControllerRow,
    DashboardRow,
    DeviceRow,
    Downsample,
    Event,
    Point,
    ProgramFormat,
    ProgramRow,
    RigVersionRow,
    SampleRow,
    Series,
    SessionKind,
    SessionRow,
    SignalRow,
    Span,
    SpanKind,
    Tick,
    TuningRow,
    Window,
    WriteRow,
    WriteStateRow,
)

__all__ = [
    "ConstraintError",
    "ControllerRow",
    "DashboardRow",
    "DeviceRow",
    "Downsample",
    "Event",
    "NotDeclaredError",
    "Point",
    "ProgramFormat",
    "ProgramRow",
    "RigVersionRow",
    "SampleRow",
    "SchemaError",
    "Series",
    "SessionEndedError",
    "SessionKind",
    "SessionNotFoundError",
    "SessionRow",
    "SessionWriter",
    "SignalRow",
    "Span",
    "SpanKind",
    "SqliteSessionWriter",
    "SqliteStore",
    "Store",
    "StoreError",
    "StoreUnavailableError",
    "Tick",
    "TuningNotFoundError",
    "TuningRow",
    "Window",
    "WriteRow",
    "WriteStateRow",
]
