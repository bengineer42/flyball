"""`POST /api/rig/stop`: the route every stop takes, and the break-glass beside it."""

from __future__ import annotations

import pytest

from conftest import TestClient
from flyball.interfaces.server import create_app
from flyball.interfaces.server.deps import current_stopper, set_stopper
from flyball.runner.stopping import Actor, StopReport, install_break_glass
from flyball.runtime.config import AuthConfig


def test_stop_is_routed_and_answers_not_wired_yet():
    with TestClient(create_app()) as http:
        response = http.post("/api/rig/stop", json={"reason": "test"})
    assert response.status_code == 501, response.text
    assert response.json() == {"detail": "stop not wired yet"}


def test_stop_takes_no_body_too():
    with TestClient(create_app()) as http:
        assert http.post("/api/rig/stop").status_code == 501


def test_stop_is_behind_the_door(monkeypatch):
    """Anonymous read is not enough: stop acts, so it needs the operator's way in."""
    monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
    app = create_app(AuthConfig(token="s3cret", anonymous="read"), secret=b"k")
    with TestClient(app) as http:
        assert http.post("/api/rig/stop").status_code in (401, 403)
        allowed = http.post("/api/rig/stop", headers={"Authorization": "Bearer s3cret"})
    assert allowed.status_code == 501


def test_no_stopper_until_one_is_set():
    assert current_stopper() is None

    class Fake:
        def stop(self, actor: Actor, reason: str) -> StopReport:
            raise AssertionError("not called")

    fake = Fake()
    set_stopper(fake)
    try:
        assert current_stopper() is fake
    finally:
        set_stopper(None)
    assert current_stopper() is None


def test_break_glass_is_a_no_op_for_now():
    called: list[object] = []
    assert install_break_glass(lambda: called.append(1)) is None  # type: ignore[func-returns-value]
    assert called == []


def test_actor_and_report_are_frozen():
    actor = Actor(sub="local:signal", sid="", kind="human", via="signal")
    report = StopReport(
        at_ns=1,
        actor=actor,
        reason="SIGUSR1",
        devices={"pump": {"state": "held", "detail": ""}},
        program_interrupted=True,
        controllers_manual=["pump.flow"],
        interim=True,
    )
    with pytest.raises(AttributeError):
        report.reason = "x"  # type: ignore[misc]
    assert actor.detail == ""
