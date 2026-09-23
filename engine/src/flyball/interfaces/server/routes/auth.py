"""`/api/auth`: the door. Who the caller is, sign in, sign out.

Always reachable, whatever the runner's settings, so the UI can ask which door
to draw before its first refused request. The login sets the session cookie
described in [flyball.interfaces.server.auth][]; the browser carries it from then on.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from flyball.interfaces.server.auth import COOKIE, Auth, Level, Principal, Scheme
from flyball.runtime.config import Anonymous

router = APIRouter(prefix="/api/auth", tags=["auth"])


class AuthOut(BaseModel):
    """Who the caller is here, and what the runner's door is like."""

    scheme: Scheme = Field(description="How the caller got in: a session, a token, or not at all.")
    level: Level = Field(description="What the caller may do: nothing, read, or operate.")
    anonymous: Anonymous = Field(description="What a caller who has not signed in may do.")
    password: bool = Field(description="Whether the runner has a password to sign in with.")
    token: bool = Field(description="Whether the runner has a bearer token for machines.")


class Login(BaseModel):
    secret: str = Field(description="The password; the runner's bearer token is taken too.")


def _auth(request: Request) -> Auth | None:
    return getattr(request.app.state, "auth", None)


def _out(request: Request) -> AuthOut:
    auth = _auth(request)
    if auth is None:  # no password, no token: the runner is open
        return AuthOut(
            scheme="anonymous", level="operate", anonymous="none", password=False, token=False
        )
    principal: Principal = request.state.auth
    return AuthOut(
        scheme=principal.scheme,
        level=principal.level,
        anonymous=auth.config.anonymous,
        password=auth.config.password is not None,
        token=auth.config.token is not None,
    )


def _set_cookie(request: Request, response: Response, value: str, max_age: int | None) -> None:
    root = request.scope.get("root_path") or ""
    response.set_cookie(
        COOKIE,
        value,
        max_age=max_age,
        path=root or "/",  # two runners on one host, one cookie each
        httponly=True,  # page scripts cannot read it
        samesite="lax",  # another site cannot post with it
        # The runner trusts no forwarded header for who is asking, but a TLS proxy's
        # `X-Forwarded-Proto: https` may mark the cookie Secure: a forged one only makes
        # the forger's own cookie stricter.
        secure="https" in (request.url.scheme, request.headers.get("x-forwarded-proto")),
    )


@router.get("")
def read_auth(request: Request) -> AuthOut:
    """Who the caller is, and whether this runner needs a password, a token, or nothing."""
    return _out(request)


@router.post("/login")
async def login(request: Request, response: Response, body: Login) -> AuthOut:
    """Trade the password (or the token) for a session cookie.

    A wrong secret is 401 after a short pause; ten wrong ones in a minute from
    one address are 429 until the minute is up.
    """
    auth = _auth(request)
    if auth is None:
        return _out(request)  # nothing to sign in to
    address = request.client.host if request.client else "?"
    if auth.attempts.blocked(address):
        raise HTTPException(status_code=429, detail="Too many wrong passwords; wait a minute")
    if not auth.is_secret(body.secret):
        auth.attempts.failure(address)
        if auth.delay:
            await asyncio.sleep(auth.delay)
        raise HTTPException(status_code=401, detail="Wrong password")
    _set_cookie(request, response, auth.sessions.mint(), int(auth.config.session_s))
    request.state.auth = Principal("password", "operate")
    return _out(request)


@router.post("/logout")
def logout(request: Request, response: Response) -> AuthOut:
    """Drop the session: the cookie is cleared; the token, if any, is untouched."""
    auth = _auth(request)
    if auth is not None:
        _set_cookie(request, response, "", 0)
        request.state.auth = auth.anonymous
    return _out(request)
