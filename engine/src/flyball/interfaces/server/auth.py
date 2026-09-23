"""Who is asking, and what they may do: the door in front of everything the runner serves.

One *principal* per request or socket, resolved by [`Auth`][flyball.interfaces.server.auth.Auth]
from, in order, a bearer token (a machine), a session cookie (a person who logged in),
and nothing (anonymous); a token that is presented and wrong is refused, never taken as
anonymous. Each principal has a *level* -- `none < read < operate` -- and each request
*needs* one: a GET or a stream under `/api`, `/ws` or `/mcp` needs `read`, anything else
there `operate` (no GET has a side effect: a bus probe is a POST); a GET outside them is
the bundled UI, which the login page is part of, and needs nothing.
`allows` compares the two; that one comparison is the only place a later scheme (several
sign-ins with levels, a part of the rig locked) has to grow.

Two checks come before any of that, against other web pages rather than other people.
An open runner (no password, no token) answers only a `Host` of `localhost`, `127.0.0.1`
or `[::1]`, so a page whose name has been pointed at loopback (DNS rebinding) is refused --
unless the run opted into serving it open on the network (`--insecure-open`: the exposure's
`open_network`), whose users reach it by a network name the runner cannot know.
And in every mode, a request that acts -- any method but GET, HEAD and OPTIONS, and every
websocket -- is refused when it carries an `Origin` that is not the runner's own (or is
`null`), unless it brings the bearer token, which a page on another site cannot have.

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
from urllib.parse import parse_qs, urlsplit

from fastapi.responses import JSONResponse

from flyball.runtime.config import AuthConfig

log = logging.getLogger(__name__)

Level = Literal["none", "read", "operate"]
Scheme = Literal["anonymous", "password", "token", "passkey"]
SESSION_SCHEMES = ("password", "passkey")
"""Schemes a session cookie can carry; anything else has no session, only a bearer form."""

LEVELS: dict[Level, int] = {"none": 0, "read": 1, "operate": 2}
COOKIE = "flyball_session"
# Behind the door. A GET anywhere else is the bundled UI (the login page included) or the
# API's own description of itself (`/docs`, `/openapi.json`), and needs nothing.
GUARDED = ("/api", "/ws", "/mcp")
# Reachable by anyone, whatever the method: the door itself.
OPEN_PATHS = ("/api/auth",)
# The names an open runner answers to: loopback, and nothing a page elsewhere can own.
LOOPBACK = ("localhost", "127.0.0.1", "::1")
# The methods that change nothing, so need no `Origin` check; everything else acts.
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
_PORTS = {"http": 80, "https": 443}

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


OPEN = Principal("anonymous", "operate")  # a runner with no password and no token
ANONYMOUS_NONE = Principal("anonymous", "none")
ANONYMOUS_READ = Principal("anonymous", "read")
PERSON = Principal("password", "operate")
MACHINE = Principal("token", "operate")
PASSKEY = Principal("passkey", "operate")


def needed(scope: Any) -> Level:
    """The level a request must have: read for a GET or a stream, operate otherwise.

    A GET or HEAD outside `/api`, `/ws` and `/mcp` needs nothing: it is the bundled UI,
    whose login page must load before anyone has signed in.
    """
    path = _path(scope)
    if _under(path, OPEN_PATHS):
        return "none"
    if scope["type"] == "websocket":
        return "read"
    if scope.get("method") in ("GET", "HEAD"):
        return "read" if _under(path, GUARDED) else "none"
    return "operate"


def allows(principal: Principal, scope: Any) -> bool:
    return LEVELS[principal.level] >= LEVELS[needed(scope)]


def _path(scope: Any) -> str:
    path: str = scope["path"]
    root: str = scope.get("root_path") or ""
    return path[len(root) :] if root and path.startswith(root) else path


def _under(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


# endregion

# region Other web pages


def _authority(netloc: str, scheme: str) -> tuple[str, int] | None:
    """`host[:port]` as `(name, port)`, lower case, the port defaulted for `scheme`."""
    try:
        parts = urlsplit("//" + netloc)
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname or parts.username is not None or parts.path or parts.query:
        return None
    return parts.hostname, port if port is not None else _PORTS[scheme]


def loopback(host: str) -> bool:
    """Whether a `Host` header names loopback: `localhost`, `127.0.0.1` or `[::1]`, any port."""
    authority = _authority(host, "http")
    return authority is not None and authority[0] in LOOPBACK


def acts(scope: Any) -> bool:
    """Whether a request can change something: a websocket, or any method but a safe one."""
    return scope["type"] == "websocket" or scope.get("method") not in SAFE_METHODS


def same_origin(origin: str | None, host: str, scheme: str) -> bool:
    """Whether a request's `Origin` is the runner's own, as the request itself reached it.

    No `Origin` is a script, the CLI or an old browser's same-origin request: allowed.
    `null` (a sandboxed frame, a `file:` page, some redirects) is refused. Otherwise the
    host and port must be the `Host` header's, and the scheme the request's -- or `https`
    for a runner reached over plain HTTP, which is a TLS proxy in front of it; the other
    way round (a page on `http://` acting on a runner reached over `https://`) is refused.
    """
    if origin is None:
        return True
    own = "https" if scheme in ("https", "wss") else "http"
    parts = urlsplit(origin)
    if parts.scheme not in _PORTS or (parts.scheme != own and own == "https"):
        return False
    theirs = _authority(parts.netloc, parts.scheme)
    return theirs is not None and theirs == _authority(host, parts.scheme)


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
    """ASGI middleware: refuse other web pages, resolve the principal, refuse what it may not do.

    Installed on every runner; with neither a password nor a token in `config` the runner
    is *open*: everyone is `OPEN` (operate), but only on a loopback `Host` -- any `Host`
    with `open_network`, the run's `--insecure-open` (the `Origin` check still holds). Puts the
    principal on `scope["state"]["auth"]` (so `request.state.auth`); on a runner with a door
    the `Auth` itself is `app.state.auth`, the routes' way to it. Refusal is 401 with a
    `detail` and `WWW-Authenticate: Bearer` like every other error, or 403 for a foreign
    `Host` or `Origin`; a socket is closed with 4401 or 4403. `internal_token` is the one
    the runner mints for its own MCP mount when there is a password but no token.
    """

    def __init__(
        self,
        app: Any,
        config: AuthConfig,
        secret: bytes,
        *,
        internal_token: str | None = None,
        delay: float = 0.5,
        open_network: bool = False,
    ) -> None:
        self.app = app
        self.config = config
        self.sessions = Sessions(secret, config.password, config.session_s)
        self.internal_token = internal_token
        self.delay = delay  # after a wrong password, before the 401
        self.attempts = Attempts()
        # Logins hashing a password right now, and how many may: a scrypt hash holds 16 MiB.
        self.hashing = 0
        self.max_hashing = 2
        self.anonymous = ANONYMOUS_READ if config.anonymous == "read" else ANONYMOUS_NONE
        self.open = not config.enabled
        # Open and served on the network by the user's choice: any `Host` is its own name.
        self.open_network = self.open and open_network

    # -- resolving --

    def principal(self, scope: Any) -> Principal | None:
        """Who is asking; `None` for a token that was presented and is wrong."""
        if self.open:
            return OPEN
        headers: dict[bytes, bytes] = dict(scope.get("headers") or [])
        if (given := self._bearer(scope, headers)) is not None:
            return MACHINE if self.is_token(given) else None
        cookie = SimpleCookie()
        cookie.load(headers.get(b"cookie", b"").decode(errors="replace"))
        if COOKIE in cookie and (scheme := self.sessions.verify(cookie[COOKIE].value)) is not None:
            if scheme.startswith("passkey"):
                return PASSKEY if self._passkey_live(scheme) else self.anonymous
            return PERSON
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
        headers: dict[bytes, bytes] = dict(scope.get("headers") or [])
        host = headers.get(b"host", b"").decode(errors="replace")
        if self.open and not self.open_network and not loopback(host):
            detail = (
                "This runner has no password or token, so it answers only to localhost,"
                " 127.0.0.1 or [::1]; give it --password or --token to reach it by another name"
            )
            await _refuse(scope, receive, send, 403, detail)
            return
        principal = self.principal(scope)
        if principal is None:
            await _refuse(scope, receive, send, 401, "Wrong token")
            return
        origin = headers.get(b"origin")
        if (
            principal.scheme != "token"
            and acts(scope)
            and not same_origin(
                None if origin is None else origin.decode(errors="replace"),
                host,
                scope.get("scheme", "http"),
            )
        ):
            detail = "Refused: the request's Origin is another site's, not this runner's"
            await _refuse(scope, receive, send, 403, detail)
            return
        scope.setdefault("state", {})["auth"] = principal
        if allows(principal, scope):
            await self.app(scope, receive, send)
            return
        detail = (
            "Sign in first (POST /api/auth/login), or send the runner's bearer token"
            " (Authorization: Bearer ...)"
        )
        await _refuse(scope, receive, send, 401, detail)


async def _refuse(scope: Any, receive: Any, send: Any, status: int, detail: str) -> None:
    """401 or 403 with a `detail`; a socket is closed with 4401 or 4403 before it opens."""
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 4000 + status, "reason": detail[:120]})
        return
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    response = JSONResponse(status_code=status, content={"detail": detail}, headers=headers)
    await response(scope, receive, send)
