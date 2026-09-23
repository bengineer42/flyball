"""`GET /api/rig/config` never hands out the runner's credentials.

The route needs only `read`, so an `anonymous: read` visitor, and the MCP `rig_config` tool, reach
it. It used to return `runner.auth` verbatim -- password, token and signing secret -- which turned
read into operate in one request (found by the adversarial review, executed live).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from conftest import TestClient
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
    assert auth == {"anonymous": "read"}, "non-secret settings stay visible; removed keys do not"
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


# region The whole `runner:` section, end to end

# Every credential and credential path a rig file may carry under `runner:`, each a value that
# appears nowhere else in the file, so finding it in a response is finding the leak.
FRONT_SECRETS = {
    "runner.auth.token": "TOK-bare-runner",
    "runner.front.password": "$scrypt$ln=15,r=8,p=1$c2FsdA$aGFzaC1vZi10aGUtcGFzc3dvcmQ",
    "runner.front.proxy.secret_file": "/etc/flyball/proxy-SECRET-FILE",
    "runner.front.proxy.jwt.jwks_url": "https://idp.internal/JWKS-URL",
    "runner.front.proxy.grants": "alice@SUBJECT.example",
    "runner.front.tls.key": "/etc/flyball/TLS-KEY.pem",
    "runner.front.tls.cert": "/etc/flyball/TLS-CERT.pem",
    "runner.front.trusted_proxies": "10.9.8.7/32",
    "runner.store": "/srv/STORE-PATH.sqlite",
    "runner.drivers": "/srv/DRIVERS-PATH",
}

SIM_RIG = f"""\
name: tank
clock: {{stepped: true}}
links:
  tank: {{tag: sim_plant, model: lag, gain: 1.0, tau_s: 10.0}}
devices:
  level:
    driver: sim_daq
    poll_s: 1.0
    link: tank
    ports: {{level: {{port: output, quantity: level, unit: m}}}}
runner:
  port: 8123
  store: {FRONT_SECRETS["runner.store"]}
  drivers: {FRONT_SECRETS["runner.drivers"]}
  auth:
    token: {FRONT_SECRETS["runner.auth.token"]}
    anonymous: read
  front:
    listen: 0.0.0.0:8443
    auth: proxy
    url: https://rig.example
    anonymous: read
    password: "{FRONT_SECRETS["runner.front.password"]}"
    trusted_proxies: ["{FRONT_SECRETS["runner.front.trusted_proxies"]}"]
    tls:
      cert: {FRONT_SECRETS["runner.front.tls.cert"]}
      key: {FRONT_SECRETS["runner.front.tls.key"]}
    proxy:
      preset: custom
      secret_file: {FRONT_SECRETS["runner.front.proxy.secret_file"]}
      grants: {{operate: ["{FRONT_SECRETS["runner.front.proxy.grants"]}"]}}
      jwt:
        header: X-Assertion
        jwks_url: {FRONT_SECRETS["runner.front.proxy.jwt.jwks_url"]}
        issuer: idp
        audience: rig
        algorithms: [RS256]
"""


@pytest.fixture
def sim_rig(tmp_path):
    """A simulated rig loaded from a file as the runner loads one, with a store and versions."""
    from flyball_sim.simulation import Simulation

    from flyball.interfaces.server.deps import set_rig, set_store
    from flyball.record.sqlite import SqliteStore
    from flyball.runner.starting import keep_versions
    from flyball.runtime.overlay import resolve_layers

    path = tmp_path / "tank.yaml"
    path.write_text(SIM_RIG)
    layered, _ = resolve_layers([path])
    config = RigConfig.model_validate(layered)
    assert config.runner is not None and config.runner.front is not None, "the file parses whole"
    rig = config.build(start=False)
    store = SqliteStore(tmp_path / "tank.sqlite")
    keep_versions(rig, store, "loaded")
    set_rig(rig)
    set_store(store)
    set_rig_config(config)
    set_simulation(Simulation(rig, config, layered, path))
    try:
        with TestClient(create_app()) as http:
            yield http
    finally:
        set_simulation(None)
        set_rig_config(None)
        set_store(None)
        set_rig(None)
        rig.close()
        store.close()


ROUTES = (
    "/api/rig/config",
    "/api/sim/config",
    "/api/rig/document",
    "/api/rig/changes",
    "/api/rig/versions",
    "/api/rig/versions/1",
)


@pytest.mark.parametrize("route", ROUTES)
def test_no_runner_credential_or_path_reaches_a_reader(sim_rig, route: str):
    response = sim_rig.get(route)
    assert response.status_code == 200, response.text
    leaked = {key: value for key, value in FRONT_SECRETS.items() if value in response.text}
    assert not leaked, f"{route} hands out {sorted(leaked)}"


def test_what_a_reader_needs_of_the_runner_section_stays(sim_rig):
    runner = sim_rig.get("/api/rig/config").json()["runner"]
    assert runner["port"] == 8123
    assert runner["auth"] == {"anonymous": "read"}
    assert runner["front"] == {
        "listen": "0.0.0.0:8443",
        "auth": "proxy",
        "url": "https://rig.example",
        "anonymous": "read",
    }


def test_an_unknown_runner_key_is_dropped_not_passed_through():
    """An allowlist all the way down: a key added to `RunnerConfig` later is hidden by default."""
    from flyball.interfaces.server.redact import without_credentials

    document = {
        "runner": {"port": 1, "new_secret": "x", "front": {"auth": "sso", "sso": {"key": "y"}}}
    }
    assert without_credentials(document) == {"runner": {"port": 1, "front": {"auth": "sso"}}}


# endregion
