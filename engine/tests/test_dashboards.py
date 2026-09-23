"""Dashboards: saved per rig with a history, renamed, deleted, imported from files."""

from __future__ import annotations

import json

import pytest

from conftest import TestClient
from flyball.control.laws import P
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.deps import set_store
from flyball.interfaces.server.routes.dashboards import import_directory, migrate, problems_for
from flyball.record.sqlite import SqliteStore
from flyball.rig import Rig
from test_server import Daq, Drive

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
            "config": {"address": "p.t"},
        },
        {"id": "b", "kind": "chart", "title": "Zones", "x": 3, "y": 0, "w": 9, "h": 6},
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
    assert body["body"]["schema_version"] == 5
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


def test_import_directory_reads_yaml_and_toml_too(tmp_path):
    """A dashboard is any of SUFFIXES, same as a rig file -- not JSON-only."""
    store = SqliteStore(tmp_path / "t.db")
    boards = tmp_path / "dashboards"
    boards.mkdir()
    (boards / "overview.yaml").write_text(
        "rig: t\ndescription: the one\nwidgets:\n"
        "  - id: a\n    kind: readout\n    x: 0\n    y: 0\n    w: 3\n    h: 2\n"
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


def test_problems_for_flags_every_missing_binding_kind(furnace_rig):
    doc = {
        "widgets": [
            {"id": "ok-readout", "kind": "readout", "config": {"address": "zone.zone1"}},
            {"id": "bad-readout", "kind": "readout", "config": {"address": "zone.zone9"}},
            {"id": "bad-gauge", "kind": "gauge", "config": {"address": "zone9.zone1"}},
            {"id": "demand", "kind": "readout", "config": {"address": "zone.setpoint"}},
            {
                "id": "mixed-chart",
                "kind": "chart",
                "config": {"addresses": ["zone.zone1", "zone.humidity"]},
            },
            {"id": "ok-loop", "kind": "loop", "config": {"controller": "heaters.heater1"}},
            {"id": "bad-loop", "kind": "loop", "config": {"controller": "heaters.heater2"}},
            {"id": "ok-device", "kind": "device", "config": {"device": "heaters"}},
            {"id": "bad-device", "kind": "device", "config": {"device": "ghost"}},
            {"id": "no-binding", "kind": "health", "config": {}},
        ]
    }
    problems = {p.widget_id: p.ref for p in problems_for(doc, furnace_rig)}
    assert problems == {
        "bad-readout": "zone.zone9",
        "bad-gauge": "zone9.zone1",
        # "demand" is not flagged: a demand is `RPW`, so it publishes like any other signal.
        "mixed-chart": "zone.humidity",
        "bad-loop": "heaters.heater2",
        "bad-device": "ghost",
    }
    reasons = {p.ref: p.reason for p in problems_for(doc, furnace_rig)}
    assert reasons["ghost"] == "ghost is not on this rig"


V1 = {
    "schema_version": 1,
    "name": "old",
    "rig": "t",
    "widgets": [
        {
            "id": "r",
            "kind": "readout",
            "x": 0,
            "y": 0,
            "w": 1,
            "h": 1,
            "config": {"channel": "zone.zone1"},
        },
        {
            "id": "g",
            "kind": "gauge",
            "x": 0,
            "y": 0,
            "w": 1,
            "h": 1,
            "config": {"channel": {"source": "zone", "measurand": "zone1"}, "max": 100},
        },
        {
            "id": "c",
            "kind": "chart",
            "x": 0,
            "y": 0,
            "w": 1,
            "h": 1,
            "config": {"channels": ["zone.zone1", {"source": "zone", "measurand": "zone2"}, 3]},
        },
        {"id": "l", "kind": "loop", "x": 0, "y": 0, "w": 1, "h": 1, "config": {"loop": "heater1"}},
        {
            "id": "a",
            "kind": "actuator",
            "x": 0,
            "y": 0,
            "w": 1,
            "h": 1,
            "config": {"actuator": "heaters"},
        },
        {"id": "h", "kind": "health", "x": 0, "y": 0, "w": 1, "h": 1, "config": {}},
    ],
}


def test_a_version_1_document_is_migrated_to_addresses_and_controllers():
    migrated = migrate(V1)
    assert migrated["schema_version"] == 5
    configs = {w["id"]: (w["kind"], w["config"]) for w in migrated["widgets"]}
    assert configs == {
        "r": ("readout", {"address": "zone.zone1"}),
        "g": ("gauge", {"address": "zone.zone1", "max": 100}),
        "c": ("chart", {"addresses": ["zone.zone1", "zone.zone2"]}),
        "l": ("loop", {"controller": "heater1"}),
        "a": ("device", {"device": "heaters"}),
        "h": ("health", {}),
    }
    assert V1["widgets"][0]["config"] == {"channel": "zone.zone1"}, "a copy; the stored one stands"
    assert migrate(migrated) is migrated, "already current: untouched"
    assert migrate({"widgets": []})["schema_version"] == 5, "no version is version 1"
    assert (migrated["readonly"], migrated["order"]) == (False, None)


def test_a_stored_version_1_document_is_migrated_on_read(client, furnace_rig):
    c, store = client
    store.save_dashboard("old", "t", V1, 1)
    read = c.get("/api/dashboards/old").json()
    assert read["body"]["schema_version"] == 5
    assert read["body"]["widgets"][0]["config"] == {"address": "zone.zone1"}
    assert read["body"]["widgets"][4]["kind"] == "device"
    assert store.dashboard("old").body["schema_version"] == 1, "what is stored is as saved"
    assert {p["widget_id"]: p["ref"] for p in read["problems"]} == {"l": "heater1"}, (
        "a loop was named by its actuator; the controller is its target's address"
    )
    listed = c.get("/api/dashboards").json()
    assert [r["body"]["schema_version"] for r in listed] == [5]
    assert c.get("/api/dashboards/old/history").json()[0]["body"]["schema_version"] == 5


def test_save_and_read_report_problems(client):
    """`PUT`/`GET` return `problems[]` beside the document rather than refusing it (§4.8).

    The fixture's bare rig has no devices, so widget "a"'s address `p.t` is always unresolved.
    """
    c, _ = client
    saved = c.put("/api/dashboards/main", json={**DOC, "name": "main"})
    expected = [{"widget_id": "a", "ref": "p.t", "reason": "p.t is not on this rig"}]
    assert saved.json()["problems"] == expected
    assert c.get("/api/dashboards/main").json()["problems"] == expected
    # Widget "b" (a chart with no `addresses` configured) has nothing to flag.
    assert all(p["widget_id"] != "b" for p in saved.json()["problems"])


def test_a_version_2_document_becomes_writable_and_unordered_and_keeps_its_bindings():
    v2 = {
        "schema_version": 2,
        "name": "d",
        "rig": "t",
        "widgets": [
            {
                "id": "r",
                "kind": "readout",
                "x": 0,
                "y": 0,
                "w": 1,
                "h": 1,
                "config": {"address": "p.t"},
            }
        ],
    }
    migrated = migrate(v2)
    assert migrated == {**v2, "schema_version": 5, "readonly": False, "order": None}
    assert v2["schema_version"] == 2, "a copy; the stored one stands"


def test_a_version_3_program_widget_s_interrupt_button_is_its_cancel_button():
    v3 = {
        "schema_version": 3,
        "readonly": False,
        "order": None,
        "widgets": [
            {"id": "p", "kind": "program", "config": {"events": 5, "interrupt": False}},
            {"id": "r", "kind": "readout", "config": {"address": "p.t", "interrupt": 1}},
        ],
    }
    widgets = migrate(v3)["widgets"]
    assert widgets[0]["config"] == {"events": 5, "cancel": False}
    assert widgets[1]["config"] == {"address": "p.t", "interrupt": 1}, "another kind's is its own"


def test_readonly_and_order_round_trip_and_are_validated(client):
    c, _ = client
    saved = c.put(
        "/api/dashboards/wall", json={**DOC, "name": "wall", "readonly": True, "order": 1.5}
    )
    assert saved.status_code == 201
    body = c.get("/api/dashboards/wall").json()["body"]
    assert (body["schema_version"], body["readonly"], body["order"]) == (5, True, 1.5)
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
