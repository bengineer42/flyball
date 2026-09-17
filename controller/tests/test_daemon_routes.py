"""`/api/daemon`: what the process allows, and stopping or restarting it where it does."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import FakeDaemon
from flyball.runtime.config import DaemonConfig
from flyball.server import create_app
from flyball.server.deps import set_daemon


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c
    set_daemon(None)


def test_without_a_daemon_the_routes_are_404(client):
    assert client.get("/api/daemon").status_code == 404
    assert client.post("/api/daemon/shutdown").status_code == 404


def test_reads_the_settings_without_the_token(client, tmp_path):
    set_daemon(
        FakeDaemon(DaemonConfig(token="s3cret", root_path="/x", allow_save=True), [tmp_path])
    )
    body = client.get("/api/daemon").json()
    assert body["root_path"] == "/x" and body["allow_save"] and not body["allow_shutdown"]
    assert body["files"] == [str(tmp_path)] and "token" not in body


def test_shutdown_and_restart_need_allow_shutdown(client):
    daemon = FakeDaemon()
    set_daemon(daemon)
    r = client.post("/api/daemon/shutdown")
    assert r.status_code == 409 and "--allow-shutdown" in r.json()["detail"]
    assert client.post("/api/daemon/restart").status_code == 409
    assert daemon.asked == []
    set_daemon(FakeDaemon(DaemonConfig(allow_shutdown=True)))
    assert client.post("/api/daemon/shutdown").status_code == 202
    assert client.post("/api/daemon/restart").status_code == 202


def test_asks_the_handle(client):
    daemon = FakeDaemon(DaemonConfig(allow_shutdown=True))
    set_daemon(daemon)
    client.post("/api/daemon/shutdown")
    client.post("/api/daemon/restart")
    assert daemon.asked == ["shutdown", "restart"]
