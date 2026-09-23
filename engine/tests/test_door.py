"""The door against other web pages: Host on an open runner, Origin on anything that acts.

The attacks these stand for were executed against a loopback runner on 23 Sep: a page on
another site posting body-less forms (no preflight) to switch a controller to manual,
interrupt the program, reload drivers and shut the runner down; DNS rebinding (a request
with the attacker's `Host`); and a cross-site websocket reading the samples.
"""

from __future__ import annotations

import sys

import pytest
from starlette.websockets import WebSocketDisconnect

from conftest import FakeRunner, TestClient
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.auth import COOKIE, hash_password
from flyball.interfaces.server.deps import set_runner
from flyball.runtime.config import AuthConfig, RunnerConfig

EVIL = "http://evil.example"
FORM = {"Content-Type": "application/x-www-form-urlencoded"}
UNSAFE = (
    "/api/controllers/heaters.heater1/manual",
    "/api/programs/interrupt",
    "/api/drivers/reload",
    "/api/runner/shutdown",
)


@pytest.fixture
def runner():
    fake = FakeRunner(RunnerConfig(allow_shutdown=True))
    set_runner(fake)
    yield fake
    set_runner(None)


def _client(rig, auth: AuthConfig | None = None, host: str = "localhost:8000") -> TestClient:
    rig.name = "t"
    set_rig(rig)
    return TestClient(create_app(auth, secret=b"k", login_delay=0), base_url=f"http://{host}")


@pytest.fixture
def open_runner(rig, runner):
    with _client(rig) as http:
        yield http
    set_rig(None)


@pytest.fixture
def password(rig, runner, monkeypatch):
    monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
    auth = AuthConfig(password=hash_password("hunter2"), token="s3cret", anonymous="read")
    with _client(rig, auth, host="pi.lab:8000") as http:
        yield http
    set_rig(None)


# region Host, on an open runner


@pytest.mark.parametrize(
    "host", ["localhost", "localhost:8000", "LocalHost:8000", "127.0.0.1:1", "[::1]", "[::1]:8000"]
)
def test_an_open_runner_answers_loopback_names(open_runner, host):
    assert open_runner.get("/api/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["localhost.evil.example", "127.0.0.1.nip.io", "[::2]:8000", ""])
def test_a_name_that_only_looks_like_loopback_is_refused(open_runner, host):
    assert open_runner.get("/api/health", headers={"Host": host}).status_code == 403


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "192.168.1.3:8000"])
def test_an_open_runner_refuses_any_other_host(rig, runner, host):
    """DNS rebinding: the attacker's page, its name now pointing at 127.0.0.1."""
    with _client(rig, host=host) as http:
        refused = http.get("/api/health")
        assert refused.status_code == 403 and "localhost" in refused.json()["detail"]
        assert (
            http.post("/api/runner/shutdown", headers={"Origin": f"http://{host}"}).status_code
            == 403
        )
        with pytest.raises(WebSocketDisconnect) as closed, http.websocket_connect("/ws/samples"):
            pass
        assert closed.value.code == 4403
    assert runner.asked == []


def test_a_runner_with_a_door_answers_any_host(password):
    assert password.get("/api/health").status_code == 200


# endregion

# region Origin, on anything that acts


@pytest.mark.parametrize("path", UNSAFE)
def test_a_cross_site_form_post_is_refused_on_an_open_runner(open_runner, runner, path):
    refused = open_runner.post(path, headers={"Origin": EVIL, **FORM})
    assert refused.status_code == 403, refused.text
    assert "Origin" in refused.json()["detail"]
    assert runner.asked == [], "the runner was not shut down"


@pytest.mark.parametrize("origin", [EVIL, "null", "https://evil.example", "http://localhost:9999"])
def test_a_foreign_or_null_origin_is_refused(open_runner, runner, origin):
    assert open_runner.post("/api/runner/shutdown", headers={"Origin": origin}).status_code == 403
    assert runner.asked == []


@pytest.mark.parametrize(
    "origin", [None, "http://localhost:8000", "http://LOCALHOST:8000", "https://localhost:8000"]
)
def test_the_runners_own_origin_or_none_acts(open_runner, runner, origin):
    """No Origin is a script or the CLI; https is a TLS proxy in front of the runner."""
    headers = {} if origin is None else {"Origin": origin}
    assert open_runner.post("/api/runner/shutdown", headers=headers).status_code == 202
    assert runner.asked == ["shutdown"]


def test_a_downgraded_origin_is_refused(rig, runner):
    """A page on `http://` cannot act on a runner reached over `https://`."""
    rig.name = "t"
    set_rig(rig)
    with TestClient(create_app(), base_url="https://localhost:8443") as http:
        headers = {"Origin": "http://localhost:8443"}
        assert http.post("/api/runner/shutdown", headers=headers).status_code == 403
    set_rig(None)


def test_a_read_from_another_origin_is_not_refused(open_runner):
    """A GET changes nothing; the browser keeps the answer from the other page anyway."""
    assert open_runner.get("/api/health", headers={"Origin": EVIL}).status_code == 200


def test_a_cross_site_websocket_is_refused(open_runner):
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        open_runner.websocket_connect("/ws/samples", headers={"Origin": EVIL}),
    ):
        pass
    assert closed.value.code == 4403
    with open_runner.websocket_connect("/ws/samples", headers={"Origin": "http://localhost:8000"}):
        pass
    with open_runner.websocket_connect("/ws/samples"):
        pass


def test_a_cookie_is_no_help_from_another_site(password, runner):
    signed = password.post(
        "/api/auth/login", json={"secret": "hunter2"}, headers={"Origin": "http://pi.lab:8000"}
    )
    assert signed.status_code == 200 and COOKIE in password.cookies
    refused = password.post("/api/runner/shutdown", headers={"Origin": EVIL, **FORM})
    assert refused.status_code == 403
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        password.websocket_connect("/ws/samples", headers={"Origin": EVIL}),
    ):
        pass
    assert closed.value.code == 4403
    assert runner.asked == []
    ok = password.post("/api/runner/shutdown", headers={"Origin": "http://pi.lab:8000"})
    assert ok.status_code == 202 and runner.asked == ["shutdown"]


def test_a_login_from_another_site_is_refused(password):
    refused = password.post("/api/auth/login", json={"secret": "hunter2"}, headers={"Origin": EVIL})
    assert refused.status_code == 403 and COOKIE not in password.cookies


def test_the_bearer_token_is_exempt(password, runner):
    """A token is never ambient: a page on another site cannot send it without knowing it."""
    headers = {"Origin": EVIL, "Authorization": "Bearer s3cret"}
    assert password.post("/api/runner/shutdown", headers=headers).status_code == 202
    assert runner.asked == ["shutdown"]
    with password.websocket_connect("/ws/samples?token=s3cret", headers={"Origin": EVIL}):
        pass


def test_a_made_up_bearer_does_not_exempt_an_open_runner(open_runner, runner):
    headers = {"Origin": EVIL, "Authorization": "Bearer anything"}
    assert open_runner.post("/api/runner/shutdown", headers=headers).status_code == 403
    assert runner.asked == []


# endregion

# region A wrong token


def test_a_wrong_bearer_is_401_not_anonymous(password):
    assert password.get("/api/health").status_code == 200, "anonymous may read"
    wrong = password.get("/api/health", headers={"Authorization": "Bearer WRONG"})
    assert wrong.status_code == 401 and "token" in wrong.json()["detail"]
    assert password.get("/api/health?token=WRONG").status_code == 401
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        password.websocket_connect("/ws/samples?token=WRONG"),
    ):
        pass
    assert closed.value.code == 4401


# endregion

# region The bundled UI behind a password


@pytest.fixture
def locked(rig, monkeypatch, tmp_path):
    """A password, nothing anonymous, and a built UI to serve."""
    monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>flyball</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(sys.modules["flyball.interfaces.server.app"], "DASHBOARD_DIST", dist)
    with _client(rig, AuthConfig(password="hunter2"), host="pi.lab:8000") as http:
        yield http
    set_rig(None)


@pytest.mark.parametrize("path", ["/", "/index.html", "/assets/app.js"])
def test_the_login_page_and_its_assets_need_no_sign_in(locked, path):
    """The UI draws its login page at `/` when `/api/auth` says the caller may see nothing."""
    served = locked.get(path)
    assert served.status_code == 200, (path, served.status_code)
    assert locked.head(path).status_code == 200


def test_a_page_the_ui_does_not_have_is_404_not_401(locked):
    assert locked.get("/login").status_code == 404


@pytest.mark.parametrize("path", ["/api/health", "/api/devices", "/ws/samples", "/mcp/read"])
def test_the_api_stays_behind_the_door(locked, path):
    if path.startswith("/ws"):
        with pytest.raises(WebSocketDisconnect) as closed, locked.websocket_connect(path):
            pass
        assert closed.value.code == 4401
    else:
        assert locked.get(path).status_code == 401
    assert locked.post("/", headers={"Origin": "http://pi.lab:8000"}).status_code == 401


# endregion
