"""The bearer token: with one, everything the daemon serves needs it; without, nothing does."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flyball.client import Rig as Client
from flyball.mcp.http import mount
from flyball.server import create_app, set_rig


@pytest.fixture
def secured(rig, monkeypatch):
    monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
    rig.name = "t"
    set_rig(rig)
    app = create_app(token="s3cret")
    http = TestClient(app)
    mount(app, _InProcess(http, token="s3cret"))
    with http:
        yield http
    set_rig(None)


class _InProcess(Client):
    def __init__(self, http: TestClient, token: str | None) -> None:
        super().__init__("http://test", token=token)
        self.http = http

    def _request(self, method, path, body=None):
        response = self.http.request(method, path, json=body, headers=self.headers)
        if response.status_code >= 400:
            from flyball.client.rig import RigError

            raise RigError(response.status_code, response.json()["detail"])
        return response.json() if response.content else None


def test_http_refused_without_the_token_and_served_with_it(secured):
    refused = secured.get("/api/health")
    assert refused.status_code == 401 and "bearer token" in refused.json()["detail"]
    assert refused.headers["www-authenticate"] == "Bearer"
    assert secured.get("/api/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert secured.get("/api/health", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_mcp_and_openapi_are_behind_it_too(secured):
    assert secured.post("/mcp/read", json={}).status_code == 401
    assert secured.get("/openapi.json").status_code == 401


def test_websocket_takes_the_token_as_a_query_parameter(secured):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as closed, secured.websocket_connect("/ws/events"):
        pass
    assert closed.value.code == 4401
    with secured.websocket_connect("/ws/events?token=s3cret"):
        pass
    with secured.websocket_connect("/ws/events", headers={"Authorization": "Bearer s3cret"}):
        pass


def test_the_client_sends_it_from_the_argument_or_the_environment(monkeypatch):
    monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
    assert Client("http://x").headers == {}
    assert Client("http://x", token="t").headers == {"Authorization": "Bearer t"}
    monkeypatch.setenv("FLYBALL_TOKEN", "env")
    assert Client("http://x").headers == {"Authorization": "Bearer env"}
    assert Client("http://x", token="").headers == {}, "an empty token means none"


def test_no_token_means_open(rig):
    set_rig(rig)
    try:
        with TestClient(create_app()) as http:
            assert http.get("/api/health").status_code == 200
    finally:
        set_rig(None)
