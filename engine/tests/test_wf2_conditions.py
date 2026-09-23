"""Signal faults stage 1: event codes and severities, the condition store and its edges."""

from __future__ import annotations

import json
import sqlite3

from flyball.foundation.device import Severity
from flyball.record.migrate import available
from flyball.record.sqlite import SqliteStore

# region Migration


def _store_at_0018(path, events: list[tuple[int, str | None, str, dict]]) -> None:
    """A database at schema 0018 with one session and `events` in the old shape."""
    connection = sqlite3.connect(path)
    files = available()
    for version in range(1, 19):
        connection.executescript("BEGIN;\n" + files[version].read_text() + "\nCOMMIT;")
    connection.executescript("""
        DELETE FROM schema_version;
        INSERT INTO schema_version (version) VALUES (18);
        INSERT INTO session (id, start_ns, end_ns, origin_ns) VALUES (1, 1000, 5000, 1000);
    """)
    connection.executemany(
        "INSERT INTO event (session_id, offset_ns, source, kind, detail) VALUES (1, ?, ?, ?, ?)",
        [(offset, source, kind, json.dumps(detail)) for offset, source, kind, detail in events],
    )
    connection.commit()
    connection.close()


def test_the_migration_renames_kind_to_code_and_level_to_a_severity_string(tmp_path):
    path = tmp_path / "old.db"
    _store_at_0018(
        path,
        [
            (10, "furnace", "offline", {"level": 40, "scope": "device", "message": "gone"}),
            (20, "bake", "step", {"level": 20, "scope": "program", "message": "one"}),
            (30, None, "note", {"x": 1}),
        ],
    )
    store = SqliteStore(path)
    events = store.events(1)
    assert [(e.code, e.source) for e in events] == [
        ("offline", "furnace"),
        ("step", "bake"),
        ("note", None),
    ]
    assert events[0].detail == {"severity": "error", "scope": "device", "message": "gone"}
    assert events[1].detail["severity"] == "info" and "level" not in events[1].detail
    assert events[2].detail == {"x": 1}, "a detail with no numeric level is left as it was"
    assert [e.code for e in store.events(1, code="offline")] == ["offline"]
    store.close()


def test_a_severity_is_ranked_as_logging_ranks_it():
    assert [s.rank for s in Severity] == [10, 20, 30, 40]
    assert str(Severity.WARNING) == "warning"


# endregion


def test_an_events_widget_level_is_migrated_to_its_severity():
    from flyball.interfaces.server.routes.dashboards import SCHEMA_VERSION, migrate

    v4 = {
        "schema_version": 4,
        "widgets": [
            {"id": "e", "kind": "events", "config": {"level": "WARNING", "limit": 5}},
            {"id": "r", "kind": "readout", "config": {"level": "kept"}},
        ],
    }
    migrated = migrate(v4)
    assert migrated["schema_version"] == SCHEMA_VERSION == 5
    assert migrated["widgets"][0]["config"] == {"severity": "warning", "limit": 5}
    assert migrated["widgets"][1]["config"] == {"level": "kept"}, "only the events widget"


# endregion

# region Wire


def test_an_event_on_the_wire_carries_a_code_and_a_lowercase_severity():
    from flyball.foundation.device import Code, Scope
    from flyball.interfaces.server.routes.events import event_out
    from flyball.rig import Rig

    rig = Rig("t")
    event = rig.event(Severity.WARNING, Scope.RIG, "t", Code.RESTORED, "hello")
    out = event_out(event)
    assert (out["code"], out["severity"]) == ("restored", "warning")
    assert "kind" not in out and "level" not in out


# endregion
