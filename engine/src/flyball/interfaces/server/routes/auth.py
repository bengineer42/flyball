"""`/api/auth`: the door. Who the caller is, sign in with the token, sign out, the token link.

A bare runner (no front) answers these; they are reachable whatever its settings, so the UI
can ask which door to draw before its first refused request. A fronted runner answers only
`/api/auth/front`, the front's readiness probe: the front answers the rest itself. The
cookie is the in-memory session described in [flyball.interfaces.server.auth][].
"""

from __future__ import annotations

import asyncio
import os
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

import flyball
from flyball.interfaces.server.auth import LINK_S, SESSION_S, Door, Scheme
from flyball.interfaces.server.deps import current_exposure
from flyball.interfaces.server.principal import Claims
from flyball.interfaces.server.verbs import READ, VOCABULARY
from flyball.runtime.config import Anonymous

router = APIRouter(prefix="/api/auth", tags=["auth"])

PROTOCOL = 1
"""The front <-> runner channel's version, reported by `/api/auth/front`."""


class ExposureOut(BaseModel):
    """Where the runner serves, against where it was asked to."""

    requested: str = Field(description="The bind address asked for.")
    host: str = Field(description="The bind address served on.")
    port: int
    open: bool = Field(description="No token: whoever reaches it may operate.")
    restricted: bool = Field(
        description="Asked for an address beyond loopback while open, so served on 127.0.0.1."
    )
    open_network: bool = Field(
        description="Open and reachable beyond this machine (`--insecure-open`): warn everyone."
    )
    warning: str | None = Field(description="What the runner said about it on stderr, if any.")
    endpoint: str | None = Field(
        default=None, description="What it binds: `tcp:<host>:<port>` or `unix:<path>`."
    )
    fronted: bool = Field(default=False, description="Started by a front (`--front-dir`).")
    notes: list[str] = Field(
        default_factory=list, description="Settings the runner ignores, one line each."
    )


class User(BaseModel):
    id: str
    name: str
    kind: Literal["human", "service", "agent"]


class Logins(BaseModel):
    password: bool = Field(description="The front offers the admin password; never here.")
    token: bool = Field(description="A token may be pasted (a bare runner with a token).")
    passkey: bool = False
    sso: str | None = None


class AuthInfo(BaseModel):
    """AuthInfo v2: who the caller is here, and what the door is like."""

    v: Literal[2] = 2
    shape: Literal["local", "password", "proxy", "bare"] = Field(
        description="`bare` for a runner with a token; `local` for one with none (open)."
    )
    scheme: Scheme = Field(description="How the caller got in.")
    user: User | None
    verbs: list[str] = Field(description="The caller's verbs on this rig, sorted.")
    anonymous: Anonymous = Field(description="What a caller with no credential gets.")
    login: Logins
    exposure: ExposureOut | None = Field(
        default=None,
        description="Where the runner serves against where it was asked to; None when not"
        " served by `flyball-runner`.",
    )


class Login(BaseModel):
    token: str = Field(description="The runner's bearer token, pasted by a person.")


class LinkOut(BaseModel):
    url: str = Field(description="Open this once, within `expires_in` seconds, to sign in.")
    expires_in: int


class FrontOut(BaseModel):
    protocol: int
    aud: str
    pid: int
    flyball: str


def _door(request: Request) -> Door:
    door: Door = request.app.state.door
    if door.fronted is not None:
        raise HTTPException(status_code=404, detail="The front answers /api/auth for this rig")
    return door


_NAMES = {"local:console": "local", "token:bare": "token"}


def _out(request: Request, claims: Claims | None, scheme: Scheme) -> AuthInfo:
    door = _door(request)
    exposure = current_exposure()
    user = None
    if claims is not None and scheme != "anonymous":
        kind: Literal["human", "service", "agent"] = claims.kind  # type: ignore[assignment]
        user = User(id=claims.sub, name=claims.nm or _NAMES.get(claims.sub, claims.sub), kind=kind)
    return AuthInfo(
        shape="local" if door.open else "bare",
        scheme=scheme,
        user=user,
        verbs=[] if claims is None else sorted(claims.scp),
        anonymous=door.config.anonymous,
        login=Logins(password=False, token=not door.open),
        exposure=None if exposure is None else ExposureOut.model_validate(exposure),
    )


def _anonymous(request: Request) -> AuthInfo:
    door = _door(request)
    scp = frozenset({READ}) if door.config.anonymous == "read" else frozenset()
    claims = door.claims(request.scope, "anon:", "", scp, "human")
    return _out(request, claims, "anonymous")


def _set_cookie(request: Request, response: Response, value: str, max_age: int) -> None:
    root = (request.scope.get("root_path") or "").rstrip("/")
    response.set_cookie(
        _door(request).cookie,
        value,
        max_age=max_age,
        path=f"{root}/",  # two runners on one host, one cookie each
        httponly=True,  # page scripts cannot read it
        samesite="lax",  # another site cannot post with it
        # The runner trusts no forwarded header for who is asking, but a TLS proxy's
        # `X-Forwarded-Proto: https` may mark the cookie Secure: a forged one only makes
        # the forger's own cookie stricter.
        secure="https" in (request.url.scheme, request.headers.get("x-forwarded-proto")),
    )


def _session_claims(request: Request, value: str) -> Claims:
    door = _door(request)
    found = door.session(value)
    assert found is not None
    return door.claims(request.scope, "token:bare", found.sid, VOCABULARY, "human")


@router.get("")
def read_auth(request: Request) -> AuthInfo:
    """Who the caller is, and whether this runner takes a token or nothing (AuthInfo v2)."""
    return _out(request, request.state.principal, request.state.scheme)


@router.post("/login")
async def login(request: Request, response: Response, body: Login) -> AuthInfo:
    """Trade the token for a session cookie, so the browser keeps no secret.

    A wrong token is 401 after a short pause; ten different wrong ones in a minute from one
    address (or a hundred wrong attempts) are 429 until the minute is up.
    """
    door = _door(request)
    if door.open:
        return _out(request, request.state.principal, request.state.scheme)  # nothing to sign in to
    address = request.client.host if request.client else "?"
    if door.attempts.blocked(address):
        raise HTTPException(
            status_code=429,
            detail="Too many wrong tokens; wait a minute",
            headers={"Retry-After": "60"},
        )
    if not door.is_token(body.token):
        door.attempts.failure(address, body.token)
        if door.delay:
            await asyncio.sleep(door.delay)
        raise HTTPException(status_code=401, detail="Wrong token")
    value = door.open_session()
    _set_cookie(request, response, value, SESSION_S)
    return _out(request, _session_claims(request, value), "session")


@router.post("/logout")
def logout(request: Request, response: Response) -> AuthInfo:
    """Drop the session: forgotten here and the cookie cleared; the token is untouched."""
    door = _door(request)
    if not door.open:
        value = request.cookies.get(door.cookie)
        if value:
            door.close_session(value)
        _set_cookie(request, response, "", 0)
        return _anonymous(request)
    return _out(request, request.state.principal, request.state.scheme)


@router.get("/link", response_class=RedirectResponse, status_code=302)
def follow_link(request: Request, n: str = "") -> Response:
    """The one-time link the runner prints: a session cookie, then the UI at `<root>/`.

    The nonce is spent on first use and lasts ten minutes; a used, expired or made-up one
    is 401. The redirect drops it from the address bar and sends no referrer.
    """
    door = _door(request)
    if door.open or not door.take_link(n):
        raise HTTPException(status_code=401, detail="This link is used, expired or wrong")
    root = (request.scope.get("root_path") or "").rstrip("/")
    response = RedirectResponse(f"{root}/", status_code=302)
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    _set_cookie(request, response, door.open_session(), SESSION_S)
    return response


@router.post("/link")
def make_link(request: Request) -> LinkOut:
    """A fresh one-time sign-in link for a person, asked for with the token (`flyball open`)."""
    door = _door(request)
    if door.open or request.state.scheme != "token":
        raise HTTPException(
            status_code=401,
            detail="A link is made with the token (Authorization: Bearer ...)",
            headers={"WWW-Authenticate": "Bearer"},
        )
    base = str(request.base_url).rstrip("/")

    return LinkOut(url=f"{base}/api/auth/link?n={door.mint_link()}", expires_in=LINK_S)


@router.get("/front")
def front(request: Request) -> FrontOut:
    """The front's readiness probe: answered only with a valid principal, on a fronted runner."""
    door: Door = request.app.state.door
    if door.fronted is None:
        raise HTTPException(status_code=404, detail="Not started by a front")
    return FrontOut(protocol=PROTOCOL, aud=door.aud, pid=os.getpid(), flyball=flyball.__version__)
