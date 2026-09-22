"""`/api/auth`: the door. Who the caller is, sign in, sign out.

Always reachable, whatever the runner's settings, so the UI can ask which door
to draw before its first refused request. The login sets the session cookie
described in [flyball.interfaces.server.auth][]; the browser carries it from then on.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from flyball.interfaces.server import deps, passkeys
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
    passkey: bool = Field(
        description="Whether this runner takes passkey sign-in at all (a door exists; say "
        "nothing about whether one is registered yet -- that would leak it to a stranger)."
    )


class Login(BaseModel):
    secret: str = Field(description="The password; the runner's bearer token is taken too.")


def _auth(request: Request) -> Auth | None:
    return getattr(request.app.state, "auth", None)


def _out(request: Request) -> AuthOut:
    auth = _auth(request)
    if auth is None:  # no password, no token: the runner is open, so no door to register one at
        return AuthOut(
            scheme="anonymous",
            level="operate",
            anonymous="none",
            password=False,
            token=False,
            passkey=False,
        )
    principal: Principal = request.state.auth
    return AuthOut(
        scheme=principal.scheme,
        level=principal.level,
        anonymous=auth.config.anonymous,
        password=auth.config.password is not None,
        token=auth.config.token is not None,
        # not config-gated like password/token -- any signed-in caller can add one, as long
        # as this runner was installed with the `passkeys` extra to verify them with
        passkey=passkeys.AVAILABLE,
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
        # uvicorn takes the scheme from x-forwarded-proto when the proxy is on loopback
        secure=request.url.scheme == "https",
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


# region Passkeys
#
# Additive to password/token: each registered credential grants the same
# `operate` level as a bearer token (flyball.interfaces.server.auth.PASSKEY). Registering
# one needs an already-authenticated caller -- the existing password login;
# there is no separate bootstrap. An open runner (no password, no token) has no
# door at all, so it takes no passkeys either: otherwise anyone passing by could
# register a credential that would still open the door once one is fitted. The
# RP ID is the request's own hostname; a runner reached under more than one name
# needs the credential registered under each.


def _rp_id(request: Request) -> str:
    return request.url.hostname or "localhost"


def _origin(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}"


def _door(request: Request) -> Auth:
    """The runner's `Auth`, or 501 without the extra, or 409 for an open runner.

    Every passkey route below reaches the door through here, so this is the one
    place either refusal has to be made.
    """
    if not passkeys.AVAILABLE:
        raise HTTPException(
            status_code=501,
            detail='This runner has no passkey support installed (pip install "flyball[passkeys]")',
        )
    auth = _auth(request)
    if auth is None:
        raise HTTPException(
            status_code=409,
            detail="This runner is open (no password, no token); passkeys need a door to open",
        )
    return auth


def _require_operate(request: Request) -> Auth:
    """The door, after a 401 unless the caller may operate.

    Passkey routes are under the open `/api/auth` prefix, so `Auth` never blocks
    them; registration and revocation gate on the caller's level themselves.
    """
    auth = _door(request)
    principal: Principal = request.state.auth
    if principal.level != "operate":
        raise HTTPException(status_code=401, detail="Sign in first")
    return auth


def _repo() -> passkeys.PasskeyRepo:
    return passkeys.repo_for(deps.current_store())


class PasskeyOut(BaseModel):
    id: int = Field(description="This runner's id for the credential; used to revoke it.")
    label: str = Field(description="What the operator called it when they registered it.")
    created_ns: int = Field(description="When it was registered, the rig clock's epoch.")
    transports: list[str] = Field(description='What the authenticator reported, e.g. "internal".')


def _passkey_out(row: Any) -> PasskeyOut:
    return PasskeyOut(
        id=row.id, label=row.label, created_ns=row.created_ns, transports=row.transports
    )


class PasskeyRegister(BaseModel):
    credential: dict[str, Any] = Field(description="The browser's PublicKeyCredential, as JSON.")
    label: str = Field(description='A name for this credential, e.g. "Ben\'s laptop".')


class PasskeyLogin(BaseModel):
    credential: dict[str, Any] = Field(description="The browser's PublicKeyCredential, as JSON.")


@router.post("/passkey/challenge")
def passkey_challenge(request: Request) -> Response:
    """A registration challenge. Needs a signed-in caller who may operate."""
    _require_operate(request)
    challenge = passkeys.challenges.issue()
    options = passkeys.registration_options(
        _repo(), rp_id=_rp_id(request), rp_name="flyball", challenge=challenge
    )
    return Response(content=passkeys.options_json(options), media_type="application/json")


@router.post("/passkey/register")
def passkey_register(request: Request, body: PasskeyRegister) -> PasskeyOut:
    """Verify the response and store the credential.

    Attestation itself is not checked: `none` is what was requested.
    """
    _require_operate(request)
    try:
        challenge = passkeys.client_data_challenge(body.credential)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail="malformed credential") from e
    if not passkeys.challenges.consume(challenge):
        raise HTTPException(status_code=400, detail="challenge expired or already used")
    try:
        row = passkeys.verify_registration(
            _repo(),
            body.credential,
            expected_challenge=challenge,
            rp_id=_rp_id(request),
            origin=_origin(request),
            label=body.label,
            now_ns=time.time_ns(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"registration did not verify: {e}") from e
    return _passkey_out(row)


def _address(request: Request) -> str:
    return request.client.host if request.client else "?"


@router.post("/passkey/login/challenge")
def passkey_login_challenge(request: Request) -> Response:
    """An authentication challenge. No prior auth needed -- this is how one signs in.

    The same address-based limiter as the password login applies, so an address
    that has failed ten times in a minute gets no new challenge either; the
    challenge cache itself is capped, so a flood costs bounded memory.
    """
    auth = _door(request)
    if auth.attempts.blocked(_address(request)):
        raise HTTPException(status_code=429, detail="Too many failed attempts; wait a minute")
    challenge = passkeys.challenges.issue()
    options = passkeys.login_options(_repo(), rp_id=_rp_id(request), challenge=challenge)
    return Response(content=passkeys.options_json(options), media_type="application/json")


@router.post("/passkey/login")
async def passkey_login(request: Request, response: Response, body: PasskeyLogin) -> AuthOut:
    """Trade a verified assertion for a session cookie bound to that credential.

    Like `login`'s cookie, but its scheme names the credential (`passkey:<id>`),
    so revoking the credential ends every session it opened.
    """
    auth = _door(request)
    address = _address(request)
    if auth.attempts.blocked(address):
        raise HTTPException(status_code=429, detail="Too many failed attempts; wait a minute")
    try:
        challenge = passkeys.client_data_challenge(body.credential)
    except (KeyError, ValueError):
        challenge = b""
    if not challenge or not passkeys.challenges.consume(challenge):
        auth.attempts.failure(address)
        raise HTTPException(status_code=400, detail="challenge expired or already used")
    try:
        row = passkeys.verify_login(
            _repo(),
            body.credential,
            expected_challenge=challenge,
            rp_id=_rp_id(request),
            origin=_origin(request),
        )
    except Exception as e:
        auth.attempts.failure(address)
        if auth.delay:
            await asyncio.sleep(auth.delay)
        raise HTTPException(status_code=401, detail="passkey did not verify") from e
    scheme = f"passkey:{passkeys.credential_ref(row.credential_id)}"
    _set_cookie(request, response, auth.sessions.mint(scheme), int(auth.config.session_s))
    request.state.auth = Principal("passkey", "operate")
    return _out(request)


class PasskeyListOut(BaseModel):
    store_backed: bool = Field(
        description="Whether this runner has a store -- false means a registered passkey does "
        "not survive a restart."
    )
    passkeys: list[PasskeyOut]


@router.get("/passkey")
def list_passkeys(request: Request) -> PasskeyListOut:
    """This runner's registered credentials -- never the public keys themselves."""
    _require_operate(request)
    return PasskeyListOut(
        store_backed=deps.current_store() is not None,
        passkeys=[_passkey_out(row) for row in _repo().passkeys()],
    )


@router.delete("/passkey/{passkey_id}")
def revoke_passkey(request: Request, passkey_id: int) -> None:
    """Forget a credential.

    Its sessions end with it: each passkey cookie names the credential it came
    from, and `Auth` looks that up on every request -- so anyone using it is
    refused from their next request, including the caller if it is their own.
    """
    _require_operate(request)
    if not _repo().delete_passkey(passkey_id):
        raise HTTPException(status_code=404, detail="no such passkey")


# endregion
