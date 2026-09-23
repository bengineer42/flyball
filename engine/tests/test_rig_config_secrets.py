"""`GET /api/rig/config` never hands out the runner's credentials.

The route needs only `read`, so an `anonymous: read` visitor, and the MCP `rig_config` tool, reach
it. It used to return `runner.auth` verbatim -- password, token and signing secret -- which turned
read into operate in one request (found by the adversarial review, executed live).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from flyball.interfaces.server import create_app
from flyball.interfaces.server.deps import set_rig_config, set_simulation
from flyball.interfaces.server.routes import rig as rig_routes
from flyball.runtime.config import RigConfig

SECRETS = ("s3cret-pw", "TOK-abc123", "fixed-signing-secret")


def _document() -> dict[str, Any]:
    return {
        "name": "hw",
        "runner": {
            "port": 8001,
            "auth": {
                "password": SECRETS[0],
                "token": SECRETS[1],
                "secret": SECRETS[2],
                "anonymous": "read",
                "session": "8h",
            },
        },
    }


def _assert_no_secrets(body: str) -> None:
    for value in SECRETS:
        assert value not in body, f"{value!r} leaked through /api/rig/config"


def test_a_loaded_rig_file_s_credentials_are_not_returned():
    set_rig_config(RigConfig.model_validate(_document()))
    try:
        with TestClient(create_app()) as http:
            response = http.get("/api/rig/config")
        assert response.status_code == 200
        _assert_no_secrets(response.text)
        # `canonical()` already leaves the runner section out of a loaded file's document
        # (`RigConfig.runner` is `exclude=True`); the leak was the simulation's document.
        assert "runner" not in response.json()
    finally:
        set_rig_config(None)


def test_a_simulation_s_document_is_redacted_too(monkeypatch: pytest.MonkeyPatch):
    fake = SimpleNamespace(config_document=_document)
    monkeypatch.setattr(rig_routes, "current_simulation", lambda: fake)
    with TestClient(create_app()) as http:
        response = http.get("/api/rig/config")
    assert response.status_code == 200
    _assert_no_secrets(response.text)
    auth = response.json()["runner"]["auth"]
    assert auth == {"anonymous": "read", "session": "8h"}, "non-secret settings stay visible"
    assert response.json()["runner"]["port"] == 8001


def test_unknown_auth_keys_are_dropped_not_passed_through(monkeypatch: pytest.MonkeyPatch):
    """An allowlist, so a credential added to `AuthConfig` later is hidden by default."""
    document = _document()
    document["runner"]["auth"]["future_credential"] = "later-secret"
    monkeypatch.setattr(
        rig_routes, "current_simulation", lambda: SimpleNamespace(config_document=lambda: document)
    )
    with TestClient(create_app()) as http:
        response = http.get("/api/rig/config")
    assert "later-secret" not in response.text


def test_the_simulation_s_own_config_route_is_redacted_too():
    """`GET /api/sim/config` hands out the same document."""
    set_simulation(SimpleNamespace(config_document=_document))  # type: ignore[arg-type]
    try:
        with TestClient(create_app()) as http:
            response = http.get("/api/sim/config")
        assert response.status_code == 200
        _assert_no_secrets(response.text)
    finally:
        set_simulation(None)
