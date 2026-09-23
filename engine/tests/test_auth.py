"""The door: a fronted runner takes the signed principal only; a bare one, its token.

Fronted mode is checked in-process and over a real uvicorn unix socket (`uds=`), the way the
front reaches it.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from starlette.websockets import WebSocketDisconnect

import flyball
from conftest import TestClient
from flyball.interfaces.client import Rig as Client
from flyball.interfaces.mcp.http import mount
from flyball.interfaces.server import auth as auth_module
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.auth import Fronted
from flyball.interfaces.server.principal import Claims, mint
from flyball.runtime.config import AuthConfig

KEY = bytes(range(32))
AUD = "run-0a1b2c3d"
BOTH = ("operate", "read")


def principal(
    scp: tuple[str, ...] = BOTH, *, key: bytes = KEY, aud: str = AUD, age: int = 0, **kw
) -> str:
    now = int(time.time()) - age
    claims = Claims(
        sub="local:admin",
        sid="s-1",
        scp=frozenset(scp),
        kind="human",
        aud=aud,
        cip="192.0.2.9",
        sch="https",
        iat=now,
        exp=now + 60,
    )
    return mint(key, replace(claims, **kw))


def signed(scp: tuple[str, ...] = BOTH, **kw) -> dict[str, str]:
    return {"X-Flyball-Principal": principal(scp, **kw)}


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


def _serve(rig, auth: AuthConfig | None, *, mcp_token=None, **kw) -> TestClient:
    rig.name = "t"
    set_rig(rig)
    app = create_app(auth, login_delay=0, **kw)
    http = TestClient(app)
    mount(app, _InProcess(http, token=mcp_token))
    return http


@pytest.fixture
def secured(rig, monkeypatch):
    """A bare runner with a token and nothing anonymous."""
    monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
    with _serve(rig, AuthConfig(token="s3cret"), mcp_token="s3cret", port=8123) as http:
        yield http
    set_rig(None)


@pytest.fixture
def public(rig, monkeypatch):
    """Anyone may read; the token to operate."""
    monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
    with _serve(rig, AuthConfig(token="s3cret", anonymous="read")) as http:
        yield http
    set_rig(None)


@pytest.fixture
def fronted(rig, monkeypatch):
    """A runner the front started, with a rig file that still says token and anonymous read."""
    monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
    auth = AuthConfig(token="s3cret", anonymous="read")
    with _serve(rig, auth, mcp_token="s3cret", front=Fronted(KEY, AUD)) as http:
        yield http
    set_rig(None)


BEARER = {"Authorization": "Bearer s3cret"}

# region Bare: the token


def test_http_refused_without_the_token_and_served_with_it(secured):
    refused = secured.get("/api/health")
    assert refused.status_code == 401 and "token" in refused.json()["detail"]
    assert refused.headers["www-authenticate"] == "Bearer"
    assert secured.get("/api/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert secured.get("/api/health", headers=BEARER).status_code == 200


def test_a_token_in_the_url_is_refused(secured):
    """It was accepted on a GET and a socket; a URL ends up in logs, history and referrers."""
    refused = secured.get("/api/health?token=s3cret")
    assert refused.status_code == 401 and "never in the URL" in refused.json()["detail"]
    assert secured.post("/mcp/read?token=s3cret", json={}).status_code == 401
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        secured.websocket_connect("/ws/events?token=s3cret"),
    ):
        pass
    assert closed.value.code == 4401


def test_websockets_take_the_header(secured):
    with pytest.raises(WebSocketDisconnect) as closed, secured.websocket_connect("/ws/events"):
        pass
    assert closed.value.code == 4401
    with secured.websocket_connect("/ws/events", headers=BEARER):
        pass


def test_authinfo_v2_for_a_bare_runner_not_signed_in(secured):
    assert secured.get("/api/auth").json() == {
        "v": 2,
        "shape": "bare",
        "scheme": "anonymous",
        "user": None,
        "verbs": [],
        "anonymous": "none",
        "login": {"password": False, "token": True, "passkey": False, "sso": None},
        "exposure": None,
    }
    mine = secured.get("/api/auth", headers=BEARER).json()
    assert mine["scheme"] == "token" and mine["verbs"] == ["operate", "read"]
    assert mine["user"] == {"id": "token:bare", "name": "token", "kind": "service"}


def test_mcp_is_behind_it_but_the_docs_and_the_door_are_not(secured):
    assert secured.post("/mcp/read", json={}).status_code == 401
    assert secured.get("/openapi.json").status_code == 200


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
            info = http.get("/api/auth").json()
            assert (info["shape"], info["scheme"], info["verbs"]) == ("local", "local", list(BOTH))
            assert info["user"]["id"] == "local:console" and not info["login"]["token"]
            assert http.post("/api/auth/login", json={"token": "x"}).status_code == 200
    finally:
        set_rig(None)


def test_an_unknown_api_path_is_refused_not_guessed(secured):
    """No row in the verb table: 403 for everyone, even the token."""
    refused = secured.get("/api/no-such-thing", headers=BEARER)
    assert refused.status_code == 403 and refused.json()["needed"] is None


# endregion

# region Bare: a pasted token, and the link


def test_a_pasted_token_logs_in_and_logout_forgets_the_session(secured):
    assert secured.post("/api/auth/login", json={"token": "wrong"}).status_code == 401
    signed_in = secured.post("/api/auth/login", json={"token": "s3cret"})
    assert signed_in.status_code == 200, signed_in.text
    info = signed_in.json()
    assert info["scheme"] == "session" and info["user"]["kind"] == "human"
    cookie = signed_in.headers["set-cookie"]
    assert cookie.startswith("flyball-bare-8123=")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Secure" not in cookie, "plain http in the test client"
    value = secured.cookies["flyball-bare-8123"]
    assert secured.get("/api/health").status_code == 200
    with secured.websocket_connect("/ws/events"):  # the cookie rides on the upgrade
        pass
    assert secured.get("/api/auth").json()["scheme"] == "session"
    out = secured.post("/api/auth/logout")
    assert out.json()["scheme"] == "anonymous"
    assert secured.get("/api/health").status_code == 401
    secured.cookies.set("flyball-bare-8123", value)
    assert secured.get("/api/health").status_code == 401, "forgotten here, not only cleared"


def test_the_old_login_body_is_refused(secured):
    assert secured.post("/api/auth/login", json={"secret": "s3cret"}).status_code == 422


def test_ten_wrong_tokens_in_a_minute_lock_the_door(secured):
    for _ in range(10):
        assert secured.post("/api/auth/login", json={"token": "no"}).status_code == 401
    assert secured.post("/api/auth/login", json={"token": "s3cret"}).status_code == 429


def test_ten_wrong_bearers_in_a_minute_lock_the_door_too(secured):
    """A guesser using the header (the CLI's and MCP's way) is counted like a login.

    It was never counted: 500 wrong bearers took 0.15 s, every one a plain 401.
    """
    for i in range(10):
        wrong = secured.get("/api/health", headers={"Authorization": f"Bearer no{i}"})
        assert wrong.status_code == 401
    blocked = secured.get("/api/health", headers=BEARER)
    assert blocked.status_code == 429, "the right token too, or the answer tells it apart"
    assert int(blocked.headers["retry-after"]) > 0
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        secured.websocket_connect("/ws/events", headers=BEARER),
    ):
        pass
    assert closed.value.code == 4429
    assert secured.post("/api/auth/login", json={"token": "s3cret"}).status_code == 429
    assert secured.get("/api/health").status_code == 401, "no bearer: not held back"


def test_the_link_is_single_use_and_leaves_no_nonce_behind(secured):
    door = secured.app.state.door
    nonce = door.mint_link()
    followed = secured.get(f"/api/auth/link?n={nonce}", follow_redirects=False)
    assert followed.status_code == 302, followed.text
    assert followed.headers["location"] == "/"
    assert followed.headers["referrer-policy"] == "no-referrer"
    assert followed.headers["cache-control"] == "no-store"
    assert followed.headers["set-cookie"].startswith("flyball-bare-8123=")
    assert secured.get("/api/health").status_code == 200
    secured.cookies.clear()
    again = secured.get(f"/api/auth/link?n={nonce}", follow_redirects=False)
    assert again.status_code == 401 and "set-cookie" not in again.headers
    assert secured.get("/api/auth/link?n=made-up", follow_redirects=False).status_code == 401


def test_the_link_expires_after_ten_minutes(secured, monkeypatch):
    door = secured.app.state.door
    nonce = door.mint_link()
    later = time.monotonic() + 601
    monkeypatch.setattr(auth_module.time, "monotonic", lambda: later)
    assert secured.get(f"/api/auth/link?n={nonce}", follow_redirects=False).status_code == 401


def test_the_token_mints_more_links(secured):
    assert secured.post("/api/auth/link").status_code == 401
    made = secured.post("/api/auth/link", headers=BEARER)
    assert made.status_code == 200 and made.json()["expires_in"] == 600
    url = made.json()["url"]
    assert url.startswith("http://localhost/api/auth/link?n=")
    assert secured.get(url, follow_redirects=False).status_code == 302
    assert secured.post("/api/auth/link").status_code == 401, "a session cannot mint links"


def test_the_link_honours_the_root_path(rig):
    rig.name = "t"
    set_rig(rig)
    try:
        with TestClient(create_app(AuthConfig(token="s3cret"), "/rigs/a")) as http:
            nonce = http.app.state.door.mint_link()
            followed = http.get(f"/rigs/a/api/auth/link?n={nonce}", follow_redirects=False)
            assert followed.headers["location"] == "/rigs/a/"
            assert "Path=/rigs/a/" in followed.headers["set-cookie"]
    finally:
        set_rig(None)


# endregion

# region Bare: anonymous read, and the runner's own principal


def test_anyone_may_read_but_only_the_token_may_operate(public):
    assert public.get("/api/health").status_code == 200
    with public.websocket_connect("/ws/events"):
        pass
    info = public.get("/api/auth").json()
    assert (info["scheme"], info["verbs"], info["anonymous"]) == ("anonymous", ["read"], "read")
    refused = public.post("/api/recording", json={})
    assert refused.status_code == 401 and "Sign in" in refused.json()["detail"]
    assert public.post("/api/probe").status_code == 401, "a bus scan"
    public.post("/api/auth/login", json={"token": "s3cret"})
    assert public.post("/api/recording", json={}).status_code not in (401, 403)


def test_a_reader_cannot_write_lines_into_the_runner_log(public, caplog):
    """`POST /api/rig/check` needs only read; a key it names reaches a warning, escaped.

    A newline in a `runner.front` key used to start a line of the attacker's own in the
    runner's log (CWE-117).
    """
    forged = "ok\n2026-09-23T00:00:00+0100 ERROR flyball.audit: INJECTED\rLINE"
    with caplog.at_level("WARNING", logger="flyball.runtime.config"):
        checked = public.post(
            "/api/rig/check", json={"name": "x", "runner": {"front": {forged: 1}}}
        )
    assert checked.status_code == 200, checked.text
    said = [r.getMessage() for r in caplog.records if r.name == "flyball.runtime.config"]
    assert said and "INJECTED" in said[0], said
    assert not any(c in m for m in said for c in "\n\r\x1b"), said


def test_a_bare_runner_takes_a_principal_it_signed_itself(public):
    """The MCP mount's inner calls: signed with the runner's in-memory key, for its `aud`."""
    door = public.app.state.door
    mine = {"X-Flyball-Principal": principal(("read",), key=door.key, aud=door.aud)}
    assert public.get("/api/devices", headers=mine).status_code == 200
    refused = public.post("/api/probe", headers=mine)
    assert refused.status_code == 403 and refused.json()["needed"] == "operate"
    forged = public.get("/api/devices", headers=signed())
    assert forged.status_code == 401 and forged.headers["x-flyball-principal-error"] == "mac"


# endregion

# region Fronted: the principal is the only credential


def test_fronted_no_principal_is_401_with_the_code(fronted):
    refused = fronted.get("/api/health")
    assert refused.status_code == 401
    assert refused.headers["x-flyball-principal-error"] == "format"
    assert fronted.get("/api/health", headers=signed()).status_code == 200


@pytest.mark.parametrize(
    "headers, code",
    [
        (lambda: signed(key=bytes(32)), "mac"),
        (lambda: signed(aud="run-other"), "aud"),
        (lambda: signed(age=120), "expired"),
        (lambda: {"X-Flyball-Principal": "v1.e30.AAAA"}, "mac"),
        (lambda: {"X-Flyball-Principal": "garbage"}, "format"),
    ],
)
def test_fronted_a_bad_principal_is_401(fronted, headers, code):
    refused = fronted.get("/api/health", headers=headers())
    assert refused.status_code == 401
    assert refused.headers["x-flyball-principal-error"] == code


def test_fronted_a_principal_for_another_runner_is_refused(rig):
    """Replayed at runner B, runner A's principal fails on `aud` even with B's key."""
    rig.name = "t"
    set_rig(rig)
    try:
        with TestClient(create_app(front=Fronted(KEY, "rig-b"))) as http:
            refused = http.get("/api/health", headers=signed(aud="rig-a"))
            assert refused.headers["x-flyball-principal-error"] == "aud"
    finally:
        set_rig(None)


def test_fronted_duplicates_and_other_spellings_are_401(fronted):
    token = principal()
    twice = fronted.get(
        "/api/health", headers=[("X-Flyball-Principal", token), ("X-Flyball-Principal", token)]
    )
    assert twice.status_code == 401
    underscored = fronted.get("/api/health", headers={"x_flyball_principal": token})
    assert underscored.status_code == 401
    beside = fronted.get("/api/health", headers={**signed(), "X-Flyball-Scopes": "operate"})
    assert beside.status_code == 401
    also = fronted.get("/api/health", headers={**signed(), "x_flyball_anything": "1"})
    assert also.status_code == 401
    assert fronted.get("/api/health", headers={**signed(), "X-Request-Id": "ab"}).status_code == 200


def test_fronted_ignores_the_token_cookie_and_anonymous(fronted):
    """F2: `runner.auth` says token and anonymous read; none of it opens a fronted runner."""
    assert fronted.get("/api/health").status_code == 401, "anonymous read ignored"
    assert fronted.get("/api/health", headers=BEARER).status_code == 401
    assert fronted.get("/api/health?token=s3cret").status_code == 401
    fronted.cookies.set("flyball-bare", "anything")
    assert fronted.get("/api/health").status_code == 401
    assert fronted.post("/api/auth/login", json={"token": "s3cret"}).status_code == 401


def test_fronted_a_principal_lacking_the_verb_is_403(fronted):
    refused = fronted.post("/api/probe", headers=signed(("read",)))
    assert refused.status_code == 403
    assert refused.json()["needed"] == "operate" and "detail" in refused.json()
    assert fronted.post("/api/rig/check", json={}, headers=signed(("read",))).status_code != 403


def test_fronted_a_websocket_is_accepted_then_closed_4401(fronted):
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        fronted.websocket_connect("/ws/events") as ws,
    ):
        ws.receive_text()
    assert closed.value.code == 4401
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        fronted.websocket_connect("/ws/events", headers=signed(())) as ws,
    ):
        ws.receive_text()
    assert closed.value.code == 4403
    with fronted.websocket_connect("/ws/events", headers=signed(("read",))) as ws:
        assert ws.receive_json() is not None


def test_fronted_auth_front_is_the_handshake(fronted):
    assert fronted.get("/api/auth/front").status_code == 401
    probe = fronted.get("/api/auth/front", headers=signed((), sub="front:probe", kind="service"))
    assert probe.status_code == 200
    assert probe.json() == {
        "protocol": 1,
        "aud": AUD,
        "pid": os.getpid(),
        "flyball": flyball.__version__,
    }
    for path in ("/api/auth", "/api/auth/link?n=x"):
        assert fronted.get(path, headers=signed()).status_code == 404, path


def test_a_bare_runner_has_no_handshake(secured):
    assert secured.get("/api/auth/front", headers=BEARER).status_code == 404


def test_fronted_serves_no_ui(rig, monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>flyball</html>")
    monkeypatch.setattr(sys.modules["flyball.interfaces.server.app"], "DASHBOARD_DIST", dist)
    set_rig(rig)
    try:
        with TestClient(create_app(front=Fronted(KEY, AUD))) as http:
            assert http.get("/", headers=signed()).status_code == 404
        with TestClient(create_app()) as http:
            assert http.get("/").status_code == 200, "a bare runner still serves it"
    finally:
        set_rig(None)


# endregion

# region Fronted, over a real unix socket


@pytest.fixture
def uds(rig) -> Iterator[Path]:
    import uvicorn

    rig.name = "t"
    set_rig(rig)
    app = create_app(AuthConfig(token="s3cret", anonymous="read"), front=Fronted(KEY, AUD))
    where = Path(tempfile.mkdtemp(prefix="fb-")) / "sock"
    server = uvicorn.Server(uvicorn.Config(app, uds=str(where), log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    try:
        yield where
    finally:
        server.should_exit = True
        thread.join(10)
        set_rig(None)


def test_over_the_socket_unsigned_is_401_and_signed_is_200(uds):
    with httpx.Client(
        transport=httpx.HTTPTransport(uds=str(uds)), base_url="http://localhost"
    ) as c:
        refused = c.get("/api/auth/front")
        assert refused.status_code == 401
        assert refused.headers["x-flyball-principal-error"] == "format"
        forged = c.get("/api/health", headers=signed(key=bytes(32)))
        assert forged.status_code == 401 and forged.headers["x-flyball-principal-error"] == "mac"
        assert c.get("/api/health", headers=BEARER).status_code == 401
        ok = c.get("/api/auth/front", headers=signed(()))
        assert ok.status_code == 200 and ok.json()["aud"] == AUD
        assert c.post("/api/probe", headers=signed(("read",))).json()["needed"] == "operate"


def test_over_the_socket_a_websocket_closes_4401_after_accept(uds):
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import unix_connect

    # The handshake completes; the close follows.
    with (
        unix_connect(str(uds), "ws://localhost/ws/events") as ws,
        pytest.raises(ConnectionClosed) as closed,
    ):
        ws.recv(timeout=5)
    assert closed.value.rcvd is not None and closed.value.rcvd.code == 4401


# endregion

# region What the routes see


def test_every_admitted_request_carries_its_principal():
    """`request.state.principal` (and `.auth`, the same): what MCP's re-mint and audit read."""
    from starlette.responses import PlainTextResponse

    from flyball.interfaces.server.auth import Door

    seen: dict = {}

    async def inner(scope, receive, send):
        seen.update(scope["state"])
        await PlainTextResponse("ok")(scope, receive, send)

    bare = TestClient(Door(inner, AuthConfig(token="s3cret"), port=1))
    assert bare.get("/api/health", headers=BEARER).status_code == 200
    claims = seen["principal"]
    assert isinstance(claims, Claims) and seen["auth"] is claims and seen["scheme"] == "token"
    assert (claims.sub, claims.kind, claims.scp) == ("token:bare", "service", frozenset(BOTH))
    assert claims.aud == bare.app.aud and claims.aud.startswith("bare-")
    fronted = TestClient(Door(inner, fronted=Fronted(KEY, AUD)))
    assert fronted.get("/api/health", headers=signed(("read",), via="mcp")).status_code == 200
    assert seen["principal"].via == "mcp" and seen["principal"].aud == AUD


# endregion

# region What is gone


def test_the_password_machinery_is_gone():
    for name in ("Sessions", "signing_secret", "hash_password", "verify_password", "Principal"):
        assert not hasattr(auth_module, name), name
    src = Path(flyball.__file__).parent
    writers = [
        str(path)
        for path in src.rglob("*.py")
        if 'with_suffix(".key")' in path.read_text() or "internal_token" in path.read_text()
    ]
    assert writers == []


# endregion
