"""Who is asking, and what they may do: the door in front of everything the runner serves.

One *principal* per request or socket, resolved by [`Auth`][flyball.interfaces.server.auth.Auth]
from, in order, a session cookie (a person who logged in), a bearer token (a machine),
and nothing (anonymous). Each principal has a *level* -- `none < read < operate` -- and
each request *needs* one: a GET or a stream needs `read`, anything else `operate`, bar the
handful of GETs with side effects, which need `operate` too. `allows` compares the two;
that one comparison is the only place a later scheme (several sign-ins with levels, a
part of the rig locked) has to grow.

The password is stored hashed (`$scrypt$…`, stdlib; `hash_password` makes the line) or
in the clear, prefix-detected like htpasswd. A session is a signed, expiring note --
`<issued>.<nonce>.<hmac>` -- so the runner keeps no table of them; the key that signs
them is derived from the runner's secret *and* the stored password, so changing the
password signs everyone out. Nothing secret is ever in a URL the UI builds; `?token=`
stays accepted on a GET and a socket for the CLI's export links.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs

from fastapi.responses import JSONResponse

from flyball.runtime.config import AuthConfig

log = logging.getLogger(__name__)

Level = Literal["none", "read", "operate"]
Scheme = Literal["anonymous", "password", "token", "passkey"]
SESSION_SCHEMES = ("password", "passkey")
"""Schemes a session cookie can carry; anything else has no session, only a bearer form."""

LEVELS: dict[Level, int] = {"none": 0, "read": 1, "operate": 2}
COOKIE = "flyball_session"
# GETs that do something: a probe scans a bus. Anonymous readers do not get these.
SIDE_EFFECT_GETS = ("/api/probe",)
# Reachable by anyone: the door itself, and the API's own description of itself.
OPEN_PATHS = ("/api/auth", "/docs", "/redoc", "/openapi.json")
# Everything the rig itself answers lives under these; a GET anywhere else is the
# dashboard bundle (`index.html`, its assets), which anyone may load -- the login
# page is part of it, and a locked runner that could not serve its own login page
# would be locked to everyone.
GUARDED_PREFIXES = ("/api", "/ws", "/mcp")

_SCRYPT = "$scrypt$"
_N, _R, _P = 16384, 8, 1


# region Passwords


def hash_password(plain: str) -> str:
    """A `$scrypt$n=…,r=…,p=…$<salt>$<hash>` line for the rig file."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(plain.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"{_SCRYPT}n={_N},r={_R},p={_P}${_b64(salt)}${_b64(digest)}"


def verify_password(plain: str, stored: str) -> bool:
    """Whether `plain` is the password `stored` holds, hashed or in the clear."""
    if not stored.startswith(_SCRYPT):
        return hmac.compare_digest(plain.encode(), stored.encode())
    try:
        params, salt, digest = stored[len(_SCRYPT) :].split("$")
        n, r, p = (int(item.split("=")[1]) for item in params.split(","))
        expected = _unb64(digest)
        got = hashlib.scrypt(plain.encode(), salt=_unb64(salt), n=n, r=r, p=p, dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# endregion

# region Sessions


class Sessions:
    """Mints and checks session cookies: `<issued>.<nonce>.<scheme>.<hmac>`, no table.

    `secret` is the runner's; the signing key is `HMAC(secret, stored password)`,
    so a changed password (or a changed secret) makes every cookie invalid at
    once. `lifetime` is in seconds. A passkey session's scheme carries the
    credential it was minted from (`passkey:<credential id>`), so revoking that
    credential ends the session too -- the check is `Auth.principal`'s.
    """

    def __init__(self, secret: bytes, password: str | None, lifetime: float) -> None:
        self.key = hmac.new(secret, (password or "").encode(), hashlib.sha256).digest()
        self.lifetime = lifetime

    def mint(self, scheme: str = "password", now: float | None = None) -> str:
        issued = int(now if now is not None else time.time())
        body = f"{issued}.{secrets.token_urlsafe(12)}.{scheme}"
        return f"{body}.{self._sign(body)}"

    def verify(self, cookie: str, now: float | None = None) -> str | None:
        """The scheme the cookie was minted with (`passkey:<id>` for a passkey), or None."""
        parts = cookie.split(".")
        if len(parts) != 4:
            return None
        issued, nonce, scheme, signature = parts
        if not hmac.compare_digest(self._sign(f"{issued}.{nonce}.{scheme}"), signature):
            return None
        try:
            age = (now if now is not None else time.time()) - int(issued)
        except ValueError:
            return None
        return scheme if 0 <= age <= self.lifetime else None

    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self.key, body.encode(), hashlib.sha256).digest())


def signing_secret(config: AuthConfig, store: Path | None) -> bytes:
    """The key sessions are signed with.

    `auth.secret`, else a key file beside the store (made on first use,
    owner-readable), else one for this process alone.
    """
    if config.secret:
        return config.secret.encode()
    if store is not None and str(store) != ":memory:":
        path = store.with_suffix(".key")
        try:
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                try:
                    os.write(fd, secrets.token_urlsafe(32).encode())
                finally:
                    os.close(fd)
            return path.read_text().strip().encode()
        except OSError as e:
            log.warning("no session key at %s (%s): sessions end with the process", path, e)
    return secrets.token_bytes(32)


# endregion

# region Principals


@dataclass(frozen=True)
class Principal:
    scheme: Scheme
    level: Level


ANONYMOUS_NONE = Principal("anonymous", "none")
ANONYMOUS_READ = Principal("anonymous", "read")
PERSON = Principal("password", "operate")
MACHINE = Principal("token", "operate")
PASSKEY = Principal("passkey", "operate")


def needed(scope: Any) -> Level:
    """The level a request must have: read for a GET or a stream, operate otherwise."""
    path = _path(scope)
    if any(path == open or path.startswith(open + "/") for open in OPEN_PATHS):
        return "none"
    if scope["type"] == "websocket":
        return "read"
    if scope.get("method") in ("GET", "HEAD") and not any(
        path == guarded or path.startswith(guarded + "/") for guarded in GUARDED_PREFIXES
    ):
        return "none"  # the dashboard bundle
    if scope.get("method") in ("GET", "HEAD") and not path.startswith(SIDE_EFFECT_GETS):
        return "read"
    return "operate"


def allows(principal: Principal, scope: Any) -> bool:
    return LEVELS[principal.level] >= LEVELS[needed(scope)]


def _path(scope: Any) -> str:
    path: str = scope["path"]
    root: str = scope.get("root_path") or ""
    return path[len(root) :] if root and path.startswith(root) else path


# endregion


class Attempts:
    """Slows a guesser: at most `limit` failed logins a minute from one address."""

    def __init__(self, limit: int = 10, window: float = 60.0) -> None:
        self.limit, self.window = limit, window
        self.failed: dict[str, deque[float]] = {}

    def blocked(self, address: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        recent = self.failed.get(address)
        if recent is None:
            return False
        while recent and now - recent[0] > self.window:
            recent.popleft()
        return len(recent) >= self.limit

    def failure(self, address: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.failed.setdefault(address, deque()).append(now)


class Auth:
    """ASGI middleware: resolve the principal, refuse what it may not do.

    Puts the principal on `scope["state"]["auth"]` (so `request.state.auth`) and the
    `Auth` itself on `scope["app"].state.auth` is the routes' way to it. Refusal is 401
    with a `detail` and `WWW-Authenticate: Bearer` like every other error; a socket is
    closed with 4401. `internal_token` is the one the runner mints for its own MCP mount
    when there is a password but no token.
    """

    def __init__(
        self,
        app: Any,
        config: AuthConfig,
        secret: bytes,
        *,
        internal_token: str | None = None,
        delay: float = 0.5,
    ) -> None:
        self.app = app
        self.config = config
        self.sessions = Sessions(secret, config.password, config.session_s)
        self.internal_token = internal_token
        self.delay = delay  # after a wrong password, before the 401
        self.attempts = Attempts()
        self.anonymous = ANONYMOUS_READ if config.anonymous == "read" else ANONYMOUS_NONE

    # -- resolving --

    def principal(self, scope: Any) -> Principal:
        headers: dict[bytes, bytes] = dict(scope.get("headers") or [])
        cookie = SimpleCookie()
        cookie.load(headers.get(b"cookie", b"").decode(errors="replace"))
        if COOKIE in cookie and (scheme := self.sessions.verify(cookie[COOKIE].value)) is not None:
            if scheme.startswith("passkey"):
                return PASSKEY if self._passkey_live(scheme) else self.anonymous
            return PERSON
        if (given := self._bearer(scope, headers)) is not None and self.is_token(given):
            return MACHINE
        return self.anonymous

    @staticmethod
    def _passkey_live(scheme: str) -> bool:
        """Whether the credential a `passkey:<id>` session was minted from is still registered.

        One point lookup per request: it is what makes revoking a passkey end its
        sessions, since the cookie itself is stateless. A cookie with no id (an
        older format) is refused: better one sign-in than a session nothing can end.

        Anything the store raises is a No. This runs inside the ASGI door, where an
        exception would be a 500 on every request the session makes -- a locked or
        closed database would take the whole runner out for its operator rather than
        asking them to sign in again. `Store` is a protocol, so what it can raise is
        not ours to enumerate; failing closed is the only safe reading of "cannot
        tell whether this credential still exists".
        """
        # routes-level modules; imported here to avoid a cycle
        from flyball.interfaces.server import deps, passkeys

        _, sep, credential = scheme.partition(":")
        if not sep or not credential:
            return False
        try:
            credential_id = _unb64(credential)
        except (ValueError, binascii.Error):
            return False
        try:
            return passkeys.repo_for(deps.current_store()).passkey(credential_id) is not None
        except Exception:
            log.warning("passkey session refused: the store could not be asked", exc_info=True)
            return False

    def _bearer(self, scope: Any, headers: dict[bytes, bytes]) -> str | None:
        auth = headers.get(b"authorization", b"").decode(errors="replace")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        if scope["type"] == "websocket" or scope.get("method") == "GET":
            tokens: list[str] = parse_qs(scope.get("query_string", b"").decode()).get("token", [])
            return tokens[0] if tokens else None
        return None

    def is_token(self, given: str) -> bool:
        for token in (self.config.token, self.internal_token):
            if token and hmac.compare_digest(given, token):
                return True
        return False

    def is_secret(self, given: str) -> bool:
        """What the login page takes: the password, or the token (a person may paste one)."""
        if self.config.password is not None and verify_password(given, self.config.password):
            return True
        return bool(self.config.token) and self.is_token(given)

    # -- serving --

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        principal = self.principal(scope)
        scope.setdefault("state", {})["auth"] = principal
        if allows(principal, scope):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401, "reason": "sign in required"})
            return
        detail = (
            "Sign in first (POST /api/auth/login), or send the runner's bearer token"
            " (Authorization: Bearer ...)"
        )
        response = JSONResponse(
            status_code=401, content={"detail": detail}, headers={"WWW-Authenticate": "Bearer"}
        )
        await response(scope, receive, send)
