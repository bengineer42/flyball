"""The door: a password for a person (a cookie session), a token for a machine, anonymous read."""

from __future__ import annotations

import stat

import pytest
from starlette.websockets import WebSocketDisconnect

from conftest import TestClient
from flyball.interfaces.client import Rig as Client
from flyball.interfaces.mcp.http import mount
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.auth import (
    COOKIE,
    Sessions,
    hash_password,
    signing_secret,
    verify_password,
)
from flyball.runtime.config import AuthConfig


class _InProcess(Client):
    def __init__(self, http: TestClient, token: str | None) -> None:
        super().__init__("http://test", token=token)
        self.http = http

    def _request(self, method, path, body=None):
        response = self.http.request(method, path, json=body, headers=self.headers)
        if response.status_code >= 400:
            from flyball.interfaces.client.rig import RigError

            raise RigError(response.status_code, response.json()["detail"])
        return response.json() if response.content else None


def _serve(rig, auth: AuthConfig | None, *, internal: str | None = None, mcp_token=None):
    rig.name = "t"
    set_rig(rig)
    app = create_app(auth, secret=b"k", internal_token=internal, login_delay=0)
    http = TestClient(app)
    mount(app, _InProcess(http, token=mcp_token))
    return http


@pytest.fixture
def secured(rig, monkeypatch):
    """The token alone, as before there was a password."""
    monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
    http = _serve(rig, AuthConfig(token="s3cret"), mcp_token="s3cret")
    with http:
        yield http
    set_rig(None)


@pytest.fixture
def password(rig, monkeypatch):
    """A hashed password and no token: the runner's MCP mount uses an internal one."""
    monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
    auth = AuthConfig(password=hash_password("hunter2"))
    http = _serve(rig, auth, internal="int3rnal", mcp_token="int3rnal")
    with http:
        yield http
    set_rig(None)


@pytest.fixture
def public(rig, monkeypatch):
    """Anyone may read; a password to operate."""
    monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
    http = _serve(rig, AuthConfig(password="plain", anonymous="read"))
    with http:
        yield http
    set_rig(None)


# region The token, as before


def test_http_refused_without_the_token_and_served_with_it(secured):
    refused = secured.get("/api/health")
    assert refused.status_code == 401 and "bearer token" in refused.json()["detail"]
    assert refused.headers["www-authenticate"] == "Bearer"
    assert secured.get("/api/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert secured.get("/api/health", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_a_get_takes_the_token_as_a_query_parameter_but_a_post_does_not(secured):
    assert secured.get("/api/health?token=s3cret").status_code == 200
    assert secured.get("/api/health?token=wrong").status_code == 401
    assert secured.post("/mcp/read?token=s3cret", json={}).status_code == 401


def test_mcp_is_behind_it_but_the_docs_and_the_door_are_not(secured):
    assert secured.post("/mcp/read", json={}).status_code == 401
    assert secured.get("/openapi.json").status_code == 200
    assert secured.get("/api/auth").json() == {
        "scheme": "anonymous",
        "level": "none",
        "anonymous": "none",
        "password": False,
        "token": True,
    }


def test_websocket_takes_the_token_as_a_query_parameter(secured):
    with pytest.raises(WebSocketDisconnect) as closed, secured.websocket_connect("/ws/events"):
        pass
    assert closed.value.code == 4401
    with secured.websocket_connect("/ws/events?token=s3cret"):
        pass
    with secured.websocket_connect("/ws/events", headers={"Authorization": "Bearer s3cret"}):
        pass


def test_the_token_signs_in_at_the_door_too(secured):
    """A person may paste the token: it becomes a cookie and the browser keeps no secret."""
    assert secured.post("/api/auth/login", json={"secret": "wrong"}).status_code == 401
    signed = secured.post("/api/auth/login", json={"secret": "s3cret"})
    assert signed.status_code == 200 and signed.json()["scheme"] == "password"
    assert COOKIE in secured.cookies
    assert secured.get("/api/health").status_code == 200


def test_the_client_sends_it_from_the_argument_or_the_environment(monkeypatch):
    monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
    assert Client("http://x").headers == {}
    assert Client("http://x", token="t").headers == {"Authorization": "Bearer t"}
    monkeypatch.setenv("FLYBALL_TOKEN", "env")
    assert Client("http://x").headers == {"Authorization": "Bearer env"}
    assert Client("http://x", token="").headers == {}, "an empty token means none"


def test_no_password_and_no_token_means_open(rig):
    set_rig(rig)
    try:
        with TestClient(create_app()) as http:
            assert http.get("/api/health").status_code == 200
            assert http.get("/api/auth").json()["level"] == "operate"
            assert http.post("/api/auth/login", json={"secret": "x"}).status_code == 200
    finally:
        set_rig(None)


# endregion

# region A password


def test_login_sets_a_cookie_that_gets_everything_and_logout_clears_it(password):
    assert password.get("/api/health").status_code == 401
    assert password.get("/api/auth").json()["password"] is True
    wrong = password.post("/api/auth/login", json={"secret": "hunter3"})
    assert wrong.status_code == 401 and COOKIE not in password.cookies
    right = password.post("/api/auth/login", json={"secret": "hunter2"})
    assert right.status_code == 200 and right.json()["level"] == "operate"
    cookie = right.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Secure" not in cookie, "plain http in the test client"
    assert password.get("/api/health").status_code == 200
    assert password.post("/api/recording/start", json={}).status_code != 401
    with password.websocket_connect("/ws/events"):  # the cookie rides on the upgrade
        pass
    assert password.get("/api/auth").json()["scheme"] == "password"
    password.post("/api/auth/logout")
    assert password.get("/api/health").status_code == 401


def test_the_runners_own_mcp_mount_gets_in_with_the_internal_token(password):
    assert password.post("/mcp/read", json={}).status_code == 401
    ok = password.post("/mcp/read", json={}, headers={"Authorization": "Bearer int3rnal"})
    assert ok.status_code != 401
    assert password.get("/api/health?token=int3rnal").status_code == 200


def test_ten_wrong_passwords_in_a_minute_lock_the_door(password):
    for _ in range(10):
        assert password.post("/api/auth/login", json={"secret": "no"}).status_code == 401
    assert password.post("/api/auth/login", json={"secret": "hunter2"}).status_code == 429


def test_a_tampered_or_expired_cookie_is_nobody(password):
    password.post("/api/auth/login", json={"secret": "hunter2"})
    good = password.cookies[COOKIE]
    password.cookies.set(COOKIE, good[:-2] + ("aa" if not good.endswith("aa") else "bb"))
    assert password.get("/api/health").status_code == 401
    sessions = Sessions(b"k", "p", lifetime=60)
    minted = sessions.mint(now=1000)
    assert sessions.verify(minted, now=1030) and not sessions.verify(minted, now=1061)
    assert not sessions.verify(minted, now=999), "from the future"
    assert not Sessions(b"k", "changed", 60).verify(minted, now=1030), "a new password"
    assert not Sessions(b"other", "p", 60).verify(minted, now=1030), "a new secret"
    assert not sessions.verify("garbage") and not sessions.verify("a.b.c")


# endregion

# region Anonymous read


def test_anyone_may_read_but_only_a_login_may_operate(public):
    assert public.get("/api/health").status_code == 200
    with public.websocket_connect("/ws/events"):
        pass
    assert public.get("/api/auth").json() == {
        "scheme": "anonymous",
        "level": "read",
        "anonymous": "read",
        "password": True,
        "token": False,
    }
    refused = public.post("/api/recording/start", json={})
    assert refused.status_code == 401 and "Sign in" in refused.json()["detail"]
    assert public.get("/api/probe").status_code == 401, "a GET with a side effect"
    public.post("/api/auth/login", json={"secret": "plain"})
    assert public.post("/api/recording/start", json={}).status_code != 401


# endregion

# region Passwords and secrets


def test_a_hashed_password_verifies_and_a_plain_one_compares():
    hashed = hash_password("hunter2")
    assert hashed.startswith("$scrypt$n=16384,r=8,p=1$")
    assert verify_password("hunter2", hashed) and not verify_password("hunter3", hashed)
    assert hash_password("hunter2") != hashed, "a fresh salt each time"
    assert verify_password("plain", "plain") and not verify_password("plain", "other")
    assert not verify_password("x", "$scrypt$broken")


def test_the_signing_secret_is_configured_or_kept_beside_the_store(tmp_path):
    assert signing_secret(AuthConfig(secret="abc"), None) == b"abc"
    store = tmp_path / "rig.sqlite"
    first = signing_secret(AuthConfig(), store)
    key_path = tmp_path / "rig.key"
    assert key_path.exists() and len(first) >= 32
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600, "never world- or group-readable"
    assert signing_secret(AuthConfig(), store) == first, "the same key next start"
    assert signing_secret(AuthConfig(), None) != signing_secret(AuthConfig(), None)


# endregion
