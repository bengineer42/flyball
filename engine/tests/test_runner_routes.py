"""`/api/runner`: what the process allows, and stopping or restarting it where it does."""

from __future__ import annotations

import pytest

from conftest import FakeRunner, TestClient
from flyball.interfaces.server import create_app
from flyball.interfaces.server.deps import set_runner
from flyball.runtime.config import RunnerConfig


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


def test_the_door_and_health_say_where_the_runner_is_exposed(client):
    from flyball.runtime.config import settle_exposure

    assert client.get("/api/auth").json()["exposure"] is None, "no runner: nothing to say"
    assert client.get("/api/health").json()["exposure"] is None
    runner = FakeRunner(RunnerConfig(host="127.0.0.1"))
    runner.exposure = settle_exposure(RunnerConfig(host="0.0.0.0", port=8123))
    set_runner(runner)
    for path in ("/api/auth", "/api/health"):
        exposure = client.get(path).json()["exposure"]
        assert exposure["host"] == "127.0.0.1" and exposure["requested"] == "0.0.0.0", path
        assert exposure["restricted"] and not exposure["open_network"], path
        assert "--insecure-open" in exposure["warning"], path
    runner.exposure = settle_exposure(RunnerConfig(host="0.0.0.0"), insecure_open=True)
    exposure = client.get("/api/auth").json()["exposure"]
    assert exposure["open_network"] and not exposure["restricted"]
