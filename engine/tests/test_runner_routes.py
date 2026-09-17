"""`/api/runner`: what the process allows, and stopping or restarting it where it does."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import FakeRunner
from flyball.runtime.config import RunnerConfig
from flyball.server import create_app
from flyball.server.deps import set_runner


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c
    set_runner(None)


def test_without_a_runner_the_routes_are_404(client):
    assert client.get("/api/runner").status_code == 404
    assert client.post("/api/runner/shutdown").status_code == 404


def test_reads_the_settings_without_the_token(client, tmp_path):
    set_runner(
        FakeRunner(RunnerConfig(token="s3cret", root_path="/x", allow_save=True), [tmp_path])
    )
    body = client.get("/api/runner").json()
    assert body["root_path"] == "/x" and body["allow_save"] and not body["allow_shutdown"]
    assert body["files"] == [str(tmp_path)] and "token" not in body


def test_shutdown_and_restart_need_allow_shutdown(client):
    runner = FakeRunner()
    set_runner(runner)
    r = client.post("/api/runner/shutdown")
    assert r.status_code == 409 and "--allow-shutdown" in r.json()["detail"]
    assert client.post("/api/runner/restart").status_code == 409
    assert runner.asked == []
    set_runner(FakeRunner(RunnerConfig(allow_shutdown=True)))
    assert client.post("/api/runner/shutdown").status_code == 202
    assert client.post("/api/runner/restart").status_code == 202


def test_asks_the_handle(client):
    runner = FakeRunner(RunnerConfig(allow_shutdown=True))
    set_runner(runner)
    client.post("/api/runner/shutdown")
    client.post("/api/runner/restart")
    assert runner.asked == ["shutdown", "restart"]
