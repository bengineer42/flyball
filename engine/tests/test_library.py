"""The program library: verbatim storage, versions, conversion on download, run."""

from __future__ import annotations

import pytest

from conftest import TestClient
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.deps import set_programmer, set_store
from flyball.record.sqlite import SqliteStore
from flyball.rig import Rig
from flyball.sequencing.programmer import Programmer

YAML = """# a comment that must survive storage
name: dry-then-hold
steps:
  - prompt: {message: "quick", timeout: {seconds: 0.01}}
"""


@pytest.fixture
def client(tmp_path):
    rig = Rig()
    store = SqliteStore(tmp_path / "t.db")
    set_rig(rig)
    set_store(store)
    set_programmer(Programmer(rig))
    with TestClient(create_app()) as c:
        yield c
    set_programmer(None)
    set_rig(None)
    set_store(None)


def test_save_verbatim_versions_and_history(client):
    r = client.put(
        "/api/programs/library/dry?notes=first cut",
        content=YAML,
        headers={"content-type": "application/yaml"},
    )
    assert r.status_code == 201
    first = r.json()
    assert first["format"] == "yaml" and first["body"] == YAML and "# a comment" in first["body"]
    assert first["notes"] == "first cut" and "label" not in first, "a version's text is its notes"

    r = client.put(
        "/api/programs/library/dry?notes=v2",
        json={
            "format": "json",
            "body": '{"name": "dry-then-hold", "steps": []}',
            "notes": {"why": "trim"},
        },
    )
    assert r.status_code == 201 and r.json()["notes"] == {"why": "trim"}, "the envelope's own wins"

    newest = client.get("/api/programs/library/dry").json()
    assert newest["format"] == "json" and newest["id"] != first["id"]
    assert [row["id"] for row in client.get("/api/programs/library/dry/history").json()] == [
        newest["id"],
        first["id"],
    ]
    assert [row["name"] for row in client.get("/api/programs/library").json()] == ["dry"]


def test_upload_needs_a_format_and_a_parseable_document(client):
    assert (
        client.put(
            "/api/programs/library/x", content="a: 1", headers={"content-type": "text/plain"}
        ).status_code
        == 415
    )
    # the name's extension can say what it is
    assert (
        client.put(
            "/api/programs/library/x.toml",
            content="a = 1\n",
            headers={"content-type": "text/plain"},
        ).status_code
        == 201
    )
    r = client.put(
        "/api/programs/library/bad", content="a: [", headers={"content-type": "application/yaml"}
    )
    assert r.status_code == 422 and "not valid yaml" in r.json()["detail"]


def test_download_converts_between_formats(client):
    client.put(
        "/api/programs/library/dry", content=YAML, headers={"content-type": "application/yaml"}
    )
    same = client.get("/api/programs/library/dry/download")
    assert same.text == YAML and same.headers["content-type"].startswith("application/yaml")
    assert 'filename="dry.yml"' in same.headers["content-disposition"]

    toml = client.get("/api/programs/library/dry/download?format=toml")
    assert (
        toml.status_code == 200
        and 'name = "dry-then-hold"' in toml.text
        and "[[steps]]" in toml.text
    )
    assert "# a comment" not in toml.text  # comments live only in the stored text

    js = client.get("/api/programs/library/dry/download?format=json").json()
    assert js["steps"] == [{"prompt": {"message": "quick", "timeout": {"seconds": 0.01}}}]


def test_check_and_run_and_delete(client):
    client.put(
        "/api/programs/library/dry", content=YAML, headers={"content-type": "application/yaml"}
    )
    check = client.get("/api/programs/library/dry/check").json()
    assert check["ok"] is True and check["normalised"]["steps"][0]["command"]["command"] == "prompt"

    run = client.post("/api/programs/library/dry/run")
    assert run.status_code == 200
    events = client.get("/api/events").json()
    codes = [e["code"] for e in events if e["scope"] == "program"]
    assert "run_from_library" in codes and "started" in codes

    assert client.delete("/api/programs/library/dry").status_code == 204
    assert client.get("/api/programs/library/dry").status_code == 404


def test_import_directory_imports_new_and_changed_files_only(client, tmp_path):
    from flyball.interfaces.server.deps import set_programs_dir

    (tmp_path / "firing.yaml").write_text(YAML)
    (tmp_path / "notes.txt").write_text("not a program")
    (tmp_path / "broken.yaml").write_text("a: [")
    set_programs_dir(tmp_path)
    try:
        first = client.post("/api/programs/library/import").json()
        assert [p["name"] for p in first] == ["firing"] and first[0]["notes"]["source"].endswith(
            "firing.yaml"
        )
        assert client.post("/api/programs/library/import").json() == []  # unchanged: nothing new
        (tmp_path / "firing.yaml").write_text(YAML + "# edited\n")
        again = client.post("/api/programs/library/import").json()
        assert len(again) == 1 and again[0]["body"].endswith("# edited\n")
        assert len(client.get("/api/programs/library/firing/history").json()) == 2
    finally:
        set_programs_dir(None)


def test_rename_moves_every_version(client):
    client.put(
        "/api/programs/library/dry", content=YAML, headers={"content-type": "application/yaml"}
    )
    client.put(
        "/api/programs/library/dry",
        content=YAML + "# v2\n",
        headers={"content-type": "application/yaml"},
    )
    client.put(
        "/api/programs/library/other", content=YAML, headers={"content-type": "application/yaml"}
    )
    assert (
        client.post("/api/programs/library/dry/rename", json={"name": "other"}).status_code == 409
    )
    moved = client.post("/api/programs/library/dry/rename", json={"name": "wet"})
    assert moved.status_code == 200 and len(moved.json()) == 2
    assert client.get("/api/programs/library/dry").status_code == 404
    assert [r["name"] for r in client.get("/api/programs/library/wet/history").json()] == [
        "wet",
        "wet",
    ]
    assert (
        client.post("/api/programs/library/nothing/rename", json={"name": "x"}).status_code == 404
    )
