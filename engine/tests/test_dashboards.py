"""Dashboards: saved per rig with a history, renamed, deleted, imported from files."""

from __future__ import annotations

import json

import pytest

from conftest import TestClient
from flyball.control.laws import P
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.deps import set_store
from flyball.interfaces.server.routes.dashboards import import_directory, problems_for
from flyball.record.sqlite import SqliteStore
from flyball.rig import Rig
from test_server import Daq, Drive

DOC = {
    "rig": "t",
    "description": "the one",
    "widgets": [
        {
            "id": "a",
            "type": "readout",
            "x": 0,
            "y": 0,
            "w": 3,
            "h": 2,
            "config": {"address": "p.t"},
        },
        {"id": "b", "type": "chart", "label": "Zones", "x": 3, "y": 0, "w": 9, "h": 6},
    ],
}


@pytest.fixture
def client(tmp_path, rig):
    rig.name = "t"
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
    assert body["body"]["schema_version"] == 6
    assert body["body"]["grid"] == {"cols": 24, "row_height": 24}
    assert body["body"]["widgets"][1]["config"] == {}
    assert body["body"]["widgets"][1]["label"] == "Zones"
    assert body["body"]["label"] is None, "no label: the name is what shows"

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
            "widgets": [{"id": "a", "type": "readout", "x": -1, "y": 0, "w": 1, "h": 1}],
        },
    )
    assert bad.status_code == 422
    assert c.put("/api/dashboards/x", json={**DOC, "name": "x", "extra": 1}).status_code == 422
    old_words = {**DOC["widgets"][0], "kind": "readout"}  # type: ignore[dict-item]
    del old_words["type"]
    assert (
        c.put("/api/dashboards/x", json={**DOC, "name": "x", "widgets": [old_words]}).status_code
        == 422
    ), "a current document says `type`, not `kind`"

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


def test_import_directory_reads_yaml_and_toml_too(tmp_path):
    """A dashboard is any of SUFFIXES, same as a rig file -- not JSON-only."""
    store = SqliteStore(tmp_path / "t.db")
    boards = tmp_path / "dashboards"
    boards.mkdir()
    (boards / "overview.yaml").write_text(
        "rig: t\ndescription: the one\nwidgets:\n"
        "  - id: a\n    type: readout\n    x: 0\n    y: 0\n    w: 3\n    h: 2\n"
        "    config:\n      address: p.t\n"
    )
    (boards / "panel.toml").write_text('rig = "t"\ndescription = "second"\nwidgets = []\n')
    rows = {r.name: r for r in import_directory(store, boards, "furnace", 1)}
    assert set(rows) == {"overview", "panel"}
    assert rows["overview"].rig == "furnace"
    assert store.dashboard("overview").body["description"] == "the one"
    assert store.dashboard("panel").body["description"] == "second"


@pytest.fixture
def furnace_rig(rig) -> Rig:
    """A rig with a DAQ, a drive and one controller, for the bindings a dashboard names."""
    daq, drive = Daq("zone"), Drive("heaters")
    rig.add_device(daq)
    rig.add_device(drive)
    rig.attach_controller(drive.signals["heater1"], daq.signals["zone1"], law=P(kp=1.0))
    return rig


def test_problems_for_flags_every_missing_binding_type(furnace_rig):
    doc = {
        "widgets": [
            {"id": "ok-readout", "type": "readout", "config": {"address": "zone.zone1"}},
            {"id": "bad-readout", "type": "readout", "config": {"address": "zone.zone9"}},
            {"id": "bad-gauge", "type": "gauge", "config": {"address": "zone9.zone1"}},
            {"id": "demand", "type": "readout", "config": {"address": "zone.setpoint"}},
            {
                "id": "mixed-chart",
                "type": "chart",
                "config": {"addresses": ["zone.zone1", "zone.humidity"]},
            },
            {"id": "ok-loop", "type": "loop", "config": {"controller": "heaters.heater1"}},
            {"id": "bad-loop", "type": "loop", "config": {"controller": "heaters.heater2"}},
            {"id": "ok-device", "type": "device", "config": {"device": "heaters"}},
            {"id": "bad-device", "type": "device", "config": {"device": "ghost"}},
            {"id": "no-binding", "type": "health", "config": {}},
        ]
    }
    problems = {p.widget_id: p.address for p in problems_for(doc, furnace_rig)}
    assert problems == {
        "bad-readout": "zone.zone9",
        "bad-gauge": "zone9.zone1",
        # "demand" is not flagged: a demand is `RPW`, so it publishes like any other signal.
        "mixed-chart": "zone.humidity",
        "bad-loop": "heaters.heater2",
        "bad-device": "ghost",
    }
    reasons = {p.address: p.reason for p in problems_for(doc, furnace_rig)}
    assert reasons["ghost"] == "ghost is not on this rig"


def test_a_document_at_another_version_is_refused_not_converted(client, tmp_path):
    """Version 6 only (D-064): a save of an older one is a 422, a file of one is skipped."""
    c, store = client
    older = {**DOC, "name": "old", "schema_version": 5}
    assert c.put("/api/dashboards/old", json=older).status_code == 422
    (tmp_path / "old.json").write_text(json.dumps(older), encoding="utf-8")
    assert import_directory(store, tmp_path, "t", 1) == []


def test_save_and_read_report_problems(client):
    """`PUT`/`GET` return `problems[]` beside the document rather than refusing it (§4.8).

    The fixture's bare rig has no devices, so widget "a"'s address `p.t` is always unresolved.
    """
    c, _ = client
    saved = c.put("/api/dashboards/main", json={**DOC, "name": "main"})
    expected = [{"widget_id": "a", "address": "p.t", "reason": "p.t is not on this rig"}]
    assert saved.json()["problems"] == expected
    assert c.get("/api/dashboards/main").json()["problems"] == expected
    # Widget "b" (a chart with no `addresses` configured) has nothing to flag.
    assert all(p["widget_id"] != "b" for p in saved.json()["problems"])


def test_a_label_is_what_shows_and_renaming_the_key_keeps_it(client):
    """`name` is the key (URLs, files); `label` is what a person sees, edited by a save."""
    c, _ = client
    c.put("/api/dashboards/wall", json={**DOC, "name": "wall"})
    relabelled = c.put("/api/dashboards/wall", json={**DOC, "name": "wall", "label": "Wall 2"})
    assert relabelled.json()["body"]["label"] == "Wall 2"
    assert [r["body"]["label"] for r in c.get("/api/dashboards/wall/history").json()] == [
        "Wall 2",
        None,
    ]
    moved = c.post("/api/dashboards/wall/rename", json={"name": "lobby"}).json()
    assert (moved[0]["name"], moved[0]["body"]["label"]) == ("lobby", "Wall 2")


def test_readonly_and_order_round_trip_and_are_validated(client):
    c, _ = client
    saved = c.put(
        "/api/dashboards/wall", json={**DOC, "name": "wall", "readonly": True, "order": 1.5}
    )
    assert saved.status_code == 201
    body = c.get("/api/dashboards/wall").json()["body"]
    assert (body["schema_version"], body["readonly"], body["order"]) == (6, True, 1.5)
    plain = c.put("/api/dashboards/plain", json={**DOC, "name": "plain"}).json()["body"]
    assert (plain["readonly"], plain["order"]) == (False, None), "the defaults"
    assert (
        c.put("/api/dashboards/bad", json={**DOC, "name": "bad", "readonly": "maybe"}).status_code
        == 422
    )
    assert (
        c.put("/api/dashboards/bad", json={**DOC, "name": "bad", "order": "first"}).status_code
        == 422
    )
