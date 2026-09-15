"""The program library: verbatim storage, versions, conversion on download, run."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flyball.db.sqlite import SqliteStore
from flyball.programmer.programmer import Programmer
from flyball.runtime.rig import Rig
from flyball.server import create_app, set_rig
from flyball.server.deps import set_programmer, set_store

YAML = """# a comment that must survive storage
name: dry-then-hold
steps:
  - wait: {seconds: 0.01}
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
    r = client.put("/api/programs/library/dry", content=YAML, headers={"content-type": "application/yaml"})
    assert r.status_code == 201
    first = r.json()
    assert first["format"] == "yaml" and first["body"] == YAML and "# a comment" in first["body"]

    r = client.put(
        "/api/programs/library/dry?label=v2",
        json={"format": "json", "body": '{"name": "dry-then-hold", "steps": []}', "notes": {"why": "trim"}},
    )
    assert r.status_code == 201 and r.json()["label"] == "v2" and r.json()["notes"] == {"why": "trim"}

    newest = client.get("/api/programs/library/dry").json()
    assert newest["format"] == "json" and newest["id"] != first["id"]
    assert [row["id"] for row in client.get("/api/programs/library/dry/history").json()] == [newest["id"], first["id"]]
    assert [row["name"] for row in client.get("/api/programs/library").json()] == ["dry"]


def test_upload_needs_a_format_and_a_parseable_document(client):
    assert client.put("/api/programs/library/x", content="a: 1", headers={"content-type": "text/plain"}).status_code == 415
    # the name's extension can say what it is
    assert client.put("/api/programs/library/x.toml", content="a = 1\n", headers={"content-type": "text/plain"}).status_code == 201
    r = client.put("/api/programs/library/bad", content="a: [", headers={"content-type": "application/yaml"})
    assert r.status_code == 422 and "not valid yaml" in r.json()["detail"]


def test_download_converts_between_formats(client):
    client.put("/api/programs/library/dry", content=YAML, headers={"content-type": "application/yaml"})
    same = client.get("/api/programs/library/dry/download")
    assert same.text == YAML and same.headers["content-type"].startswith("application/yaml")
    assert 'filename="dry.yml"' in same.headers["content-disposition"]

    toml = client.get("/api/programs/library/dry/download?format=toml")
    assert toml.status_code == 200 and 'name = "dry-then-hold"' in toml.text and "[[steps]]" in toml.text
    assert "# a comment" not in toml.text  # comments live only in the stored text

    js = client.get("/api/programs/library/dry/download?format=json").json()
    assert js["steps"] == [{"wait": {"seconds": 0.01}}]


def test_check_and_run_and_delete(client):
    client.put("/api/programs/library/dry", content=YAML, headers={"content-type": "application/yaml"})
    check = client.get("/api/programs/library/dry/check").json()
    assert check["ok"] is True and check["normalised"]["steps"][0]["command"]["command"] == "wait"

    run = client.post("/api/programs/library/dry/run")
    assert run.status_code == 200
    events = client.get("/api/events").json()
    kinds = [e["kind"] for e in events if e["scope"] == "program"]
    assert "run_from_library" in kinds and "started" in kinds

    assert client.delete("/api/programs/library/dry").status_code == 204
    assert client.get("/api/programs/library/dry").status_code == 404
