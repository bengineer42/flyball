"""Dashboards: saved per rig with a history, renamed, deleted, imported from files."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from flyball.db.sqlite import SqliteStore
from flyball.runtime.rig import Rig
from flyball.server import create_app, set_rig
from flyball.server.deps import set_store
from flyball.server.routes.dashboards import import_directory, problems_for

DOC = {
    "rig": "t",
    "description": "the one",
    "widgets": [
        {
            "id": "a",
            "kind": "readout",
            "x": 0,
            "y": 0,
            "w": 3,
            "h": 2,
            "config": {"channel": "p.t"},
        },
        {"id": "b", "kind": "chart", "title": "Zones", "x": 3, "y": 0, "w": 9, "h": 6},
    ],
}


@pytest.fixture
def client(tmp_path):
    rig = Rig("t")
    store = SqliteStore(tmp_path / "t.db")
    set_rig(rig)
    set_store(store)
    with TestClient(create_app()) as c:
        yield c, store
    set_rig(None)
    set_store(None)


def test_save_read_history_list(client):
    c, _ = client
    saved = c.put("/api/dashboards/main", json={"name": "x", **DOC})
    assert saved.status_code == 201
    body = saved.json()
    assert body["name"] == "main" and body["rig"] == "t" and body["body"]["name"] == "main"
    assert body["body"]["grid"] == {"cols": 24, "row_height": 24}
    assert body["body"]["widgets"][1]["config"] == {}

    again = c.put("/api/dashboards/main", json={**DOC, "name": "main", "description": "v2"})
    assert again.json()["id"] != body["id"]
    assert c.get("/api/dashboards/main").json()["body"]["description"] == "v2"
    assert [r["body"]["description"] for r in c.get("/api/dashboards/main/history").json()] == [
        "v2",
        "the one",
    ]

    # The list is this rig's by default; another rig's shows with `every`.
    c.put("/api/dashboards/other", json={**DOC, "name": "other", "rig": "elsewhere"})
    assert [r["name"] for r in c.get("/api/dashboards").json()] == ["main"]
    assert [r["name"] for r in c.get("/api/dashboards?every=true").json()] == ["main", "other"]

    schema = c.get("/api/dashboards/schema").json()
    assert "widgets" in schema["properties"]


def test_validation_rename_delete(client):
    c, _ = client
    bad = c.put(
        "/api/dashboards/x",
        json={
            **DOC,
            "name": "x",
            "widgets": [{"id": "a", "kind": "readout", "x": -1, "y": 0, "w": 1, "h": 1}],
        },
    )
    assert bad.status_code == 422
    assert c.put("/api/dashboards/x", json={**DOC, "name": "x", "extra": 1}).status_code == 422

    c.put("/api/dashboards/x", json={**DOC, "name": "x"})
    moved = c.post("/api/dashboards/x/rename", json={"name": "y"})
    assert moved.status_code == 200 and moved.json()[0]["body"]["name"] == "y"
    assert c.get("/api/dashboards/x").status_code == 404
    c.put("/api/dashboards/z", json={**DOC, "name": "z"})
    assert c.post("/api/dashboards/y/rename", json={"name": "z"}).status_code == 409
    assert c.delete("/api/dashboards/y").status_code == 204
    assert c.delete("/api/dashboards/y").status_code == 404


def test_import_directory_is_idempotent_and_versions_changed_files(tmp_path):
    store = SqliteStore(tmp_path / "t.db")
    boards = tmp_path / "dashboards"
    boards.mkdir()
    (boards / "overview.json").write_text(json.dumps(DOC))
    (boards / "broken.json").write_text("{not json")
    (boards / "notes.txt").write_text("ignored")
    assert [r.name for r in import_directory(store, boards, "furnace", 1)] == ["overview"]
    assert import_directory(store, boards, "furnace", 2) == []
    assert store.dashboard("overview").rig == "furnace"
    (boards / "overview.json").write_text(json.dumps({**DOC, "description": "edited"}))
    assert len(import_directory(store, boards, "furnace", 3)) == 1
    assert len(store.dashboard_history("overview")) == 2


@dataclass
class _Measurand:
    """A bare stand-in: `problems_for` reads only `.name`."""

    name: str


@dataclass
class _Channel:
    measurand: _Measurand

    def __init__(self, measurand: str) -> None:
        self.measurand = _Measurand(measurand)


@dataclass
class _Source:
    name: str
    channels: list[_Channel] = field(default_factory=list)

    def __init__(self, name: str, measurands: list[str]) -> None:
        self.name = name
        self.channels = [_Channel(m) for m in measurands]


@dataclass
class _FakeRig:
    """Just enough of `Rig` for `problems_for`: sources with channels, `in` on loops/actuators."""

    sources: list[_Source]
    loops: set[str]
    actuators: dict[str, None] = field(default_factory=dict)

    def __init__(self, sources: list[_Source], loops: set[str], actuators: set[str]) -> None:
        self.sources = sources
        self.loops = loops
        self.actuators = dict.fromkeys(actuators)


def test_problems_for_flags_every_missing_binding_kind():
    rig = _FakeRig(
        sources=[_Source("zone1", ["temperature"])], loops={"heater1"}, actuators={"heater1"}
    )
    doc = {
        "widgets": [
            {"id": "ok-readout", "kind": "readout", "config": {"channel": "zone1.temperature"}},
            {"id": "bad-readout", "kind": "readout", "config": {"channel": "zone9.temperature"}},
            {
                "id": "bad-gauge",
                "kind": "gauge",
                "config": {"channel": {"source": "zone9", "measurand": "temperature"}},
            },
            {
                "id": "mixed-chart",
                "kind": "chart",
                "config": {"channels": ["zone1.temperature", "zone9.humidity"]},
            },
            {"id": "ok-loop", "kind": "loop", "config": {"loop": "heater1"}},
            {"id": "bad-loop", "kind": "loop", "config": {"loop": "ghost"}},
            {"id": "ok-actuator", "kind": "actuator", "config": {"actuator": "heater1"}},
            {"id": "bad-actuator", "kind": "actuator", "config": {"actuator": "ghost"}},
            {"id": "no-binding", "kind": "health", "config": {}},
        ]
    }
    problems = {p.widget_id: p.ref for p in problems_for(doc, rig)}
    assert problems == {
        "bad-readout": "zone9.temperature",
        "bad-gauge": "zone9.temperature",
        "mixed-chart": "zone9.humidity",
        "bad-loop": "ghost",
        "bad-actuator": "ghost",
    }
    reasons = {p.ref: p.reason for p in problems_for(doc, rig)}
    assert reasons["ghost"] == "ghost is not on this rig"


def test_save_and_read_report_problems(client):
    """`PUT`/`GET` return `problems[]` beside the document rather than refusing it (§4.8).

    The fixture's bare rig has no sources, so widget "a"'s channel `p.t` is always unresolved.
    """
    c, _ = client
    saved = c.put("/api/dashboards/main", json={**DOC, "name": "main"})
    expected = [{"widget_id": "a", "ref": "p.t", "reason": "p.t is not on this rig"}]
    assert saved.json()["problems"] == expected
    assert c.get("/api/dashboards/main").json()["problems"] == expected
    # Widget "b" (a chart with no `channels` configured) has nothing to flag.
    assert all(p["widget_id"] != "b" for p in saved.json()["problems"])
