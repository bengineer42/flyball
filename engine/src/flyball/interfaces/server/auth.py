"""Who is asking, and what they may do: the door in front of everything the runner serves.

Every admitted request carries a principal (`principal.Claims` on `request.state.principal`,
and how it got in on `request.state.scheme`), and every request needs the verb
`verbs.needed` names for its route. A route with no row is refused (403), never guessed.

The door works in one of two modes.

**Fronted** (the runner was started with `--front-dir`): the signed `X-Flyball-Principal`
the front mints is the only credential. `runner.auth`, a bearer token, a cookie, `?token=`
and anonymous access are all ignored. A request with no principal, more than one, another
`x-flyball-*` header (any spelling, `_` for `-` included) or one that does not verify is
401 with `X-Flyball-Principal-Error: <code>`; a websocket is accepted and closed with 4401.
A principal lacking the route's verb is 403 `{"detail", "needed"}` (4403) -- for the front's
anonymous visitor (`anon:`) a socket's upgrade is refused 403 without a handshake, which the
front closes with 4401 (sign in). Host and Origin are the front's business: it sends
`Host: localhost` and never forwards `Origin`.

**Bare** (no front: a laptop, a container, the public demo): a *token* is the one
credential. A machine sends it as `Authorization: Bearer`; a person trades it, or a one-time
link the runner prints at start (`/api/auth/link?n=`), for a session cookie held in memory.
With neither, a caller is anonymous and gets what `runner.auth.anonymous` says: nothing, or
read. With no token at all the runner is *open*: whoever reaches it gets every verb, and it
answers only a loopback `Host` (`localhost`, `127.0.0.1`, `[::1]`) so a page whose name was
pointed at loopback (DNS rebinding) is refused. Served open on the network by the run's
choice (`--insecure-open`), it answers a *known* name on every route: an IP address,
`localhost`, or this machine's own name (`hostname`, `<hostname>.local`) -- names a page
elsewhere cannot own. An anonymous caller on a runner with a token is held to the same
names on everything that needs a verb; `/api/auth` and the UI's own files answer any name,
so a person can still sign in, and a credential is served by any name (D-043). In every bare
mode a request that acts (any method but GET, HEAD and OPTIONS, and every websocket) with an
`Origin` that is not the runner's own, or `null`, is refused -- unless it brings the token
or a principal, which a page on another site cannot have. `?token=` is refused: nothing
secret goes in a URL but the one-time nonce. The runner's own MCP calls carry a principal it
signed with its in-memory key.

A wrong token, as a login or a Bearer, counts against the caller's address (`Attempts`):
ten *different* wrong tokens in a minute, or a hundred wrong attempts of any kind, and every
Bearer and login from that address is 429 until the count ages below both.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import logging
import secrets
import socket
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from typing import Any, Final, Literal
from urllib.parse import parse_qs, urlsplit

from fastapi.responses import JSONResponse
from starlette.routing import compile_path

from flyball.interfaces.server import verbs
from flyball.interfaces.server.principal import (
    ANONYMOUS,
    ERROR_HEADER,
    HEADER,
    LIFETIME,
    Claims,
    Refused,
    verify,
)
from flyball.runtime.config import AuthConfig

Scheme = Literal["local", "anonymous", "session", "token", "proxy"]
"""How a caller got in: AuthInfo v2's `scheme`."""

log = logging.getLogger(__name__)

# The names an open runner answers to: loopback, and nothing a page elsewhere can own.
LOOPBACK: Final = ("localhost", "127.0.0.1", "::1")
# The methods that change nothing, so need no `Origin` check; everything else acts.
SAFE_METHODS: Final = ("GET", "HEAD", "OPTIONS")
SHORT_TOKEN: Final = 22
"""A bare token shorter than this is warned about at start: `secrets.token_urlsafe(16)`'s
length, about 128 bits."""
SESSION_S: Final = 12 * 3600
"""How long a bare runner's session cookie lasts."""
LINK_S: Final = 600
"""How long a token-link nonce lasts."""
_PORTS = {"http": 80, "https": 443}
_FLYBALL = "x-flyball-"
# Every path the verb table knows, whatever the method: a known path asked with a method it
# does not have is 405, as the router would say, not a refusal.
_KNOWN = tuple(compile_path(rule.path)[0] for rule in verbs.TABLE)


def _known_path(scope: Any) -> bool:
    path: str = scope["path"]
    root: str = scope.get("root_path") or ""
    path = path[len(root) :] if root and path.startswith(root) else path
    return any(pattern.match(path) for pattern in _KNOWN)


@dataclass(frozen=True)
class Fronted:
    """What the front-dir gave a fronted runner: the principal key and the audience."""

    key: bytes
    aud: str


@dataclass
class _Session:
    sid: str
    expires: float  # time.monotonic()


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


def own_names() -> frozenset[str]:
    """This machine's names: `hostname`, its first label, and each with `.local` (mDNS)."""
    name = socket.gethostname().lower()
    names = {name, name.split(".")[0]} - {""}
    return frozenset(names | {f"{n}.local" for n in names})


def known_host(host: str, names: frozenset[str]) -> bool:
    """Whether a `Host` header is a name no page elsewhere can own, any port.

    An IP address, a loopback name, or one of `names` (this machine's own). Anything else
    is a DNS name, which whoever owns it may point at this runner (DNS rebinding).
    """
    authority = _authority(host, "http")
    if authority is None:
        return False
    name = authority[0]
    if name in LOOPBACK or name in names:
        return True
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


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
    """Slows a guesser: `limit` different wrong tokens, or `flood` wrong attempts, a minute.

    Per address, over a rolling `window`. A bare runner counts both ways of guessing,
    `POST /api/auth/login` and a wrong `Authorization: Bearer`; while an address is
    blocked, both are 429 for it, the right token included (else the answer would tell it
    apart). Different values, because a guesser never repeats one and a script left with an
    old token repeats nothing else: it is one guess, and never locks its address out.
    `flood` still bounds the repeats.

    A blocked request is never counted (`blocked` only lets the counts fall, as they age),
    so a block lasts until the window has passed over enough of what caused it.
    """

    def __init__(self, limit: int = 10, window: float = 60.0, flood: int = 100) -> None:
        self.limit, self.window, self.flood = limit, window, flood
        self.failed: dict[str, deque[float]] = {}
        """By address: when each wrong attempt came, oldest first."""
        self.values: dict[str, dict[bytes, float]] = {}
        """By address: each different wrong token's digest, and when it last came."""

    def blocked(self, address: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        recent = self.failed.get(address)
        if recent is None:
            return False
        while recent and now - recent[0] > self.window:
            recent.popleft()
        if not recent:  # every value came at one of these times, so none is left either
            del self.failed[address]
            self.values.pop(address, None)
            return False
        seen = self.values.get(address, {})
        for digest in [d for d, at in seen.items() if now - at > self.window]:
            del seen[digest]
        return len(seen) >= self.limit or len(recent) >= self.flood

    def failure(self, address: str, value: str, now: float | None = None) -> None:
        """Count a wrong token, `value`, from `address`; only its digest is kept."""
        now = time.monotonic() if now is None else now
        self.failed.setdefault(address, deque()).append(now)
        self.values.setdefault(address, {})[_digest(value)] = now


def _needs_a_verb(scope: Any) -> bool:
    """Whether a request is the rig's (a verb or refused), not the door's or the UI's files."""
    try:
        return verbs.needed(scope) is not None
    except verbs.Unmapped:
        return True


def _address(scope: Any) -> str:
    """The peer's address, as the login route's `request.client.host` names it."""
    client = scope.get("client")
    return client[0] if client else "?"


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


@dataclass
class Door:
    """ASGI middleware: the door, fronted or bare (see the module's description).

    `app.state.door` is the instance, the routes' way to it; `key` and `aud` are what the
    runner signs its own principals with (MCP's inner calls): the front-dir's when
    fronted, made in memory when bare. `port` names the bare session cookie
    (`flyball-bare-<port>`); `delay` is the pause after a wrong token, before the 401.
    """

    app: Any
    config: AuthConfig = field(default_factory=AuthConfig)
    fronted: Fronted | None = None
    port: int | None = None
    delay: float = 0.5
    open_network: bool = False
    key: bytes = field(default=b"", init=False)
    aud: str = field(default="", init=False)
    names: frozenset[str] = field(default=frozenset(), init=False)
    """This machine's own names (`own_names`), known to a caller with no credential."""
    attempts: Attempts = field(default_factory=Attempts, init=False)
    _sessions: dict[bytes, _Session] = field(default_factory=dict, init=False)
    _links: dict[bytes, float] = field(default_factory=dict, init=False)
    _sockets: dict[str, set[Callable[[], None]]] = field(default_factory=dict, init=False)
    """By session id: what closes each of its open websockets (thread-safe to call)."""

    def __post_init__(self) -> None:
        if self.fronted is not None:
            self.key, self.aud = self.fronted.key, self.fronted.aud
        else:
            self.key = secrets.token_bytes(32)
            self.aud = f"bare-{secrets.token_hex(4)}"
        # Open and served on the network by the user's choice: a known name, not only loopback.
        self.open_network = self.open and self.open_network
        self.names = own_names()
        self._token_sid = secrets.token_urlsafe(12)
        token = self.config.token
        if self.fronted is None and token and len(token) < SHORT_TOKEN:
            log.warning(
                "the runner's token is %d characters: a short token can be guessed; use at"
                " least %d random ones (python -c 'import secrets;"
                " print(secrets.token_urlsafe(16))'), or run it under `flyball run`",
                len(token),
                SHORT_TOKEN,
            )

    @property
    def open(self) -> bool:
        """A bare runner with no token: whoever reaches it may do everything."""
        return self.fronted is None and not self.config.enabled

    @property
    def cookie(self) -> str:
        return "flyball-bare" if self.port is None else f"flyball-bare-{self.port}"

    # -- the bare runner's credentials --

    def is_token(self, given: str) -> bool:
        token = self.config.token
        return bool(token) and hmac.compare_digest(given.encode(), token.encode())  # type: ignore[union-attr]

    def open_session(self) -> str:
        """A new session cookie's value; only its hash is kept."""
        value = secrets.token_urlsafe(32)
        self._sweep()
        self._sessions[_digest(value)] = _Session(
            secrets.token_urlsafe(12), time.monotonic() + SESSION_S
        )
        return value

    def close_session(self, value: str) -> None:
        """Forget the session, and close its open websockets (4401)."""
        found = self._sessions.pop(_digest(value), None)
        if found is not None:
            for end in list(self._sockets.get(found.sid, ())):
                end()

    def session(self, value: str) -> _Session | None:
        found = self._sessions.get(_digest(value))
        if found is None or found.expires < time.monotonic():
            return None
        return found

    def mint_link(self) -> str:
        """A one-time nonce for `/api/auth/link?n=`, good for `LINK_S` seconds."""
        nonce = secrets.token_urlsafe(24)
        self._sweep()
        self._links[_digest(nonce)] = time.monotonic() + LINK_S
        return nonce

    def take_link(self, nonce: str) -> bool:
        """Whether `nonce` is a live link; it is spent either way."""
        expires = self._links.pop(_digest(nonce), None)
        return expires is not None and expires >= time.monotonic()

    def _sweep(self) -> None:
        now = time.monotonic()
        for digest in [d for d, s in self._sessions.items() if s.expires < now]:
            del self._sessions[digest]
        for digest in [d for d, e in self._links.items() if e < now]:
            del self._links[digest]

    # -- principals --

    def claims(self, scope: Any, sub: str, sid: str, scp: frozenset[str], kind: str) -> Claims:
        """A principal for a bare runner's caller, in the shape the front's would have."""
        now = int(time.time())
        client = scope.get("client")
        return Claims(
            sub=sub,
            sid=sid,
            scp=scp,
            kind=kind,
            aud=self.aud,
            cip=client[0] if client else "",
            sch="https" if scope.get("scheme") in ("https", "wss") else "http",
            iat=now,
            exp=now + LIFETIME,
        )

    def _bare(self, scope: Any, headers: dict[bytes, bytes]) -> tuple[Claims, Scheme] | str:
        """Who is asking a bare runner, or 401 for a presented and wrong credential."""
        everything = verbs.VOCABULARY
        if self.open:
            return self.claims(
                scope, "local:console", secrets.token_urlsafe(12), everything, "human"
            ), "local"
        auth = headers.get(b"authorization", b"").decode(errors="replace")
        if auth.lower().startswith("bearer "):
            if not self.is_token(given := auth[7:].strip()):
                self.attempts.failure(_address(scope), given)
                return "Wrong token"
            return self.claims(scope, "token:bare", self._token_sid, everything, "service"), "token"
        if "token" in parse_qs(scope.get("query_string", b"").decode(errors="replace")):
            return (
                "A token goes in the Authorization header (Bearer), never in the URL;"
                " a browser signs in with it (POST /api/auth/login) or the runner's link"
            )
        cookie = SimpleCookie()
        cookie.load(headers.get(b"cookie", b"").decode(errors="replace"))
        if self.cookie in cookie and (found := self.session(cookie[self.cookie].value)):
            return self.claims(scope, "token:bare", found.sid, everything, "human"), "session"
        scp = frozenset({verbs.READ}) if self.config.anonymous == "read" else frozenset()
        return self.claims(scope, ANONYMOUS, secrets.token_urlsafe(12), scp, "human"), "anonymous"

    # -- serving --

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if self.fronted is not None:
            await self._serve_fronted(scope, receive, send)
        else:
            await self._serve_bare(scope, receive, send)

    async def _serve_fronted(self, scope: Any, receive: Any, send: Any) -> None:
        given = [
            (name, value)
            for name, value in scope.get("headers") or []
            if name.decode("latin-1").lower().replace("_", "-").startswith(_FLYBALL)
        ]
        tokens = [value for name, value in given if name == HEADER.encode()]
        try:
            if len(tokens) != 1 or len(given) != 1:
                raise Refused("format")  # none, two, or a spelling the front never sends
            claims = verify(tokens[0].decode("latin-1"), self.key, self.aud)
        except Refused as e:
            detail = f"No valid principal from the front ({e.code})"
            await _refuse(scope, receive, send, 401, detail, code=e.code, accept=True)
            return
        await self._admit(scope, receive, send, claims, "proxy", accept=True)

    async def _serve_bare(self, scope: Any, receive: Any, send: Any) -> None:
        headers: dict[bytes, bytes] = dict(scope.get("headers") or [])
        host = headers.get(b"host", b"").decode(errors="replace")
        if self.open and not self.open_network and not loopback(host):
            detail = (
                "This runner has no token, so it answers only to localhost, 127.0.0.1 or"
                " [::1]; give it --token to reach it by another name, or run it under"
                " `flyball run`"
            )
            await _refuse(scope, receive, send, 403, detail)
            return
        if self.open and not known_host(host, self.names):  # --insecure-open: every route
            detail = (
                f"This runner has no token, so it answers only {self._known()} -- not a DNS"
                " name, which a page elsewhere could point at it; give it --token to reach it"
                " by another name, or run it under `flyball run`"
            )
            await _refuse(scope, receive, send, 403, detail)
            return
        signed = headers.get(HEADER.encode())
        if signed is not None:  # the runner's own MCP call, signed with its in-memory key
            try:
                claims = verify(signed.decode("latin-1"), self.key, self.aud)
            except Refused as e:
                detail = f"Not a principal this runner signed ({e.code})"
                await _refuse(scope, receive, send, 401, detail, code=e.code)
                return
            await self._admit(scope, receive, send, claims, "token")
            return
        bearer = headers.get(b"authorization", b"")[:7].lower() == b"bearer "
        if bearer and self.attempts.blocked(_address(scope)):
            await _refuse(scope, receive, send, 429, "Too many wrong tokens; wait a minute")
            return
        resolved = self._bare(scope, headers)
        if isinstance(resolved, str):
            await _refuse(scope, receive, send, 401, resolved)
            return
        claims, scheme = resolved
        if (
            scheme == "anonymous"
            and claims.scp  # with nothing to serve, the sign-in 401 below says more
            and _needs_a_verb(scope)
            and not known_host(host, self.names)
        ):
            detail = (
                f"Without a credential this runner answers only {self._known()}; to reach it"
                " by another name, sign in (POST /api/auth/login with the token, or the link"
                " the runner printed) or send the token (Authorization: Bearer ...)"
            )
            await _refuse(scope, receive, send, 403, detail)
            return
        origin = headers.get(b"origin")
        if (
            scheme != "token"
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
        await self._admit(scope, receive, send, claims, scheme)

    def _known(self) -> str:
        names = ", ".join(sorted(self.names))
        return f"an IP address, localhost or this machine's name ({names})"

    async def _admit(
        self,
        scope: Any,
        receive: Any,
        send: Any,
        claims: Claims,
        scheme: Scheme,
        *,
        accept: bool = False,
    ) -> None:
        """Serve the request if `claims` holds the verb its route needs; refuse it otherwise.

        The principal goes on the request's state before the check, so the action audit
        (outside the door) knows who was refused too.
        """
        state = scope.setdefault("state", {})
        state["principal"] = claims
        state["auth"] = claims  # the name routes read before the principal (stop's actor)
        state["scheme"] = scheme
        try:
            needed = verbs.needed(scope)
        except verbs.Unmapped:
            if scope["type"] == "http" and _known_path(scope):
                response = JSONResponse(status_code=405, content={"detail": "Method Not Allowed"})
                await response(scope, receive, send)
                return
            detail = "No rule says who may do this, so no one may"
            await _refuse(scope, receive, send, 403, detail, needed=None, accept=accept)
            return
        if verbs.allows(claims.scp, scope):
            if scheme == "session" and scope["type"] == "websocket":
                await self._held(scope, receive, send, claims.sid)
            else:
                await self.app(scope, receive, send)
            return
        if scheme == "anonymous":
            detail = (
                "Sign in first (POST /api/auth/login with the token, or the link the runner"
                " printed), or send the token (Authorization: Bearer ...)"
            )
            await _refuse(scope, receive, send, 401, detail)
            return
        detail = f"This needs {needed!r}, which the caller does not hold here"
        # The front's visitor with no credential: a plain 403, which the front answers as
        # sign-in (a socket closed 4401); after a handshake it could only pass on a 4403.
        accept = accept and claims.sub != ANONYMOUS
        await _refuse(scope, receive, send, 403, detail, needed=needed, accept=accept)

    async def _held(self, scope: Any, receive: Any, send: Any, sid: str) -> None:
        """A session's websocket, closed with 4401 when the session ends: logout or expiry."""
        found = next((s for s in list(self._sessions.values()) if s.sid == sid), None)
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        if found is None or task is None:
            return
        ended = False
        closed = False

        def end_here() -> None:
            nonlocal ended
            if not ended:
                ended = True
                task.cancel()

        def end() -> None:  # called from logout, which runs on a worker thread
            loop.call_soon_threadsafe(end_here)

        async def sending(message: Any) -> None:
            nonlocal closed
            closed = closed or message["type"] == "websocket.close"
            await send(message)

        expiry = loop.call_later(max(0.0, found.expires - time.monotonic()), end_here)
        held = self._sockets.setdefault(sid, set())
        held.add(end)
        try:
            await self.app(scope, receive, sending)
        except asyncio.CancelledError:
            if not ended:
                raise
            task.uncancel()
        finally:
            expiry.cancel()
            held.discard(end)
            if not held:
                self._sockets.pop(sid, None)
        if ended and not closed:
            await send({"type": "websocket.close", "code": 4401, "reason": "Signed out"})


_UNSET: Any = object()


async def _refuse(
    scope: Any,
    receive: Any,
    send: Any,
    status: int,
    detail: str,
    *,
    code: str | None = None,
    needed: str | None = _UNSET,
    accept: bool = False,
) -> None:
    """401 or 403 with a `detail`; a socket is closed with 4401 or 4403.

    With `accept` the socket's handshake completes first, so the client sees the code (the
    front passes it on); without, it is closed before it opens. `code` is the principal's
    refusal code, sent as `X-Flyball-Principal-Error`; `needed` the verb that was missing.
    """
    if scope["type"] == "websocket":
        if accept:
            message = await receive()
            if message["type"] != "websocket.connect":
                return
            await send({"type": "websocket.accept"})
        await send({"type": "websocket.close", "code": 4000 + status, "reason": detail[:120]})
        return
    headers: dict[str, str] = {}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if status == 429:
        headers["Retry-After"] = "60"
    if code is not None:
        headers[ERROR_HEADER] = code
    content: dict[str, Any] = {"detail": detail}
    if needed is not _UNSET:
        content["needed"] = needed
    response = JSONResponse(status_code=status, content=content, headers=headers)
    await response(scope, receive, send)
