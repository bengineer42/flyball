"""`--root-path`: everything the daemon serves sits under one prefix; the proxy need not rewrite."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flyball import daemon
from flyball.mcp.http import mount
from flyball.server import create_app, set_rig
from test_auth import _InProcess


@pytest.fixture
def prefixed(rig):
    rig.name = "t"
    set_rig(rig)
    app = create_app(root_path="/flyball/humidity/")  # a trailing slash is tolerated
    http = TestClient(app)
    mount(app, _InProcess(http, token=None))
    with http:
        yield http
    set_rig(None)


def test_routes_answer_under_the_prefix_and_nowhere_else(prefixed):
    assert prefixed.get("/flyball/humidity/api/health").json()["rig"] == "t"
    assert prefixed.get("/flyball/humidity/openapi.json").status_code == 200
    assert prefixed.get("/api/health").status_code == 404
    assert prefixed.get("/flyball/humidityx/api/health").status_code == 404
    assert prefixed.post("/flyball/humidity/mcp/read", json={}).status_code != 404


def test_docs_link_the_prefixed_openapi(prefixed):
    assert "/flyball/humidity/openapi.json" in prefixed.get("/flyball/humidity/docs").text


def test_websockets_too(prefixed):
    from starlette.websockets import WebSocketDisconnect

    with prefixed.websocket_connect("/flyball/humidity/ws/events") as ws:
        assert ws.receive_json() is not None
    with pytest.raises(WebSocketDisconnect) as e, prefixed.websocket_connect("/ws/events"):
        pass
    assert e.value.code == 4404


def test_root_path_must_be_absolute():
    with pytest.raises(ValueError, match="start with"):
        create_app(root_path="flyball")


def test_daemon_flag_and_env(monkeypatch):
    monkeypatch.delenv("FLYBALL_ROOT_PATH", raising=False)
    assert daemon.parser().parse_args([]).root_path is None
    assert daemon.parser().parse_args(["--root-path", "/x"]).root_path == "/x"
    monkeypatch.setenv("FLYBALL_ROOT_PATH", "/y")
    assert daemon.parser().parse_args([]).root_path == "/y"
