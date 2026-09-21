"""Passkeys: an additional door scheme, not a replacement for password or token.

Each registered credential grants `Principal("passkey", "operate")` -- the same
level a bearer token grants, just bound to an authenticator instead of a shared
secret. There is no user/level table: any passkey this runner holds gets in.

Storage follows [flyball.db.sqlite][] when a store is attached (a
`passkey_credential` table, migrated like everything else there), so
credentials survive a restart. A runner with no store attached -- `store` is
genuinely optional, see [flyball.server.deps][] -- keeps credentials in an
in-process [MemoryPasskeyRepo][flyball.server.passkeys.MemoryPasskeyRepo]
instead: they work for the life of the process, same as `signing_secret`
falling back to a key made fresh each run when there is nowhere to keep one.
A passkey registered on a store-less runner does not survive a restart; this
is documented, not hidden.

The registration/authentication *challenge* is shorter-lived still: it only
has to survive one browser round trip, so it lives in
[ChallengeCache][flyball.server.passkeys.ChallengeCache], an in-process,
one-time-use set with a generous TTL. It is never persisted.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from typing import Any, Protocol

import webauthn
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    PublicKeyCredentialCreationOptions,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialRequestOptions,
)

from flyball.db.types import PasskeyRow

CHALLENGE_TTL_S = 300.0  # one browser round trip, generously
CHALLENGE_CAP = 256  # live challenges at once; a flood evicts the oldest, never grows memory


class PasskeyRepo(Protocol):
    """What a passkey route needs of storage: the subset `db.store.Store` also provides.

    `db.sqlite.SqliteStore` satisfies this directly. [MemoryPasskeyRepo][] is
    the fallback for a runner with no store.
    """

    def passkey_user_handle(self) -> bytes: ...

    def add_passkey(
        self,
        credential_id: bytes,
        public_key: bytes,
        label: str,
        created_ns: int,
        aaguid: bytes | None = None,
        transports: list[str] | tuple[str, ...] = (),
    ) -> PasskeyRow: ...

    def passkeys(self) -> list[PasskeyRow]: ...

    def passkey(self, credential_id: bytes) -> PasskeyRow | None: ...

    def update_passkey_sign_count(self, credential_id: bytes, sign_count: int) -> None: ...

    def delete_passkey(self, passkey_id: int) -> bool: ...


class MemoryPasskeyRepo:
    """Passkeys for a store-less runner: kept for the life of the process, no further."""

    def __init__(self) -> None:
        import secrets

        self._handle = secrets.token_bytes(32)
        self._rows: dict[bytes, PasskeyRow] = {}
        self._next_id = 1

    def passkey_user_handle(self) -> bytes:
        return self._handle

    def add_passkey(
        self,
        credential_id: bytes,
        public_key: bytes,
        label: str,
        created_ns: int,
        aaguid: bytes | None = None,
        transports: list[str] | tuple[str, ...] = (),
    ) -> PasskeyRow:
        row = PasskeyRow(
            id=self._next_id,
            credential_id=credential_id,
            public_key=public_key,
            sign_count=0,
            aaguid=aaguid,
            transports=list(transports),
            label=label,
            created_ns=created_ns,
        )
        self._rows[credential_id] = row
        self._next_id += 1
        return row

    def passkeys(self) -> list[PasskeyRow]:
        return sorted(self._rows.values(), key=lambda r: (r.created_ns, r.id))

    def passkey(self, credential_id: bytes) -> PasskeyRow | None:
        return self._rows.get(credential_id)

    def update_passkey_sign_count(self, credential_id: bytes, sign_count: int) -> None:
        row = self._rows.get(credential_id)
        if row is not None:
            self._rows[credential_id] = replace(row, sign_count=sign_count)

    def delete_passkey(self, passkey_id: int) -> bool:
        for credential_id, row in list(self._rows.items()):
            if row.id == passkey_id:
                del self._rows[credential_id]
                return True
        return False


_memory_repo = MemoryPasskeyRepo()


def repo_for(store: Any | None) -> PasskeyRepo:
    """The repo for this request.

    `store` itself when there is one (it already implements `PasskeyRepo`); else
    the process-lifetime fallback, shared by every request this runner serves.
    """
    return store if store is not None else _memory_repo


def reset_memory_repo() -> None:
    """Start the store-less fallback over, empty. For tests; a real runner never calls this."""
    global _memory_repo
    _memory_repo = MemoryPasskeyRepo()


class ChallengeCache:
    """One-time-use WebAuthn challenges, in-process, gone after `ttl` seconds either way.

    Holds at most `cap` at once: the login challenge is issued to anyone (that is
    how one signs in), so an unauthenticated flood must cost memory bounded by
    `cap`, not by the flood. Insertion order is expiry order, so the sweep stops
    at the first live one.
    """

    def __init__(self, ttl: float = CHALLENGE_TTL_S, cap: int = CHALLENGE_CAP) -> None:
        self._ttl = ttl
        self._cap = cap
        self._live: dict[bytes, float] = {}

    def issue(self) -> bytes:
        self._sweep()
        while len(self._live) >= self._cap:
            del self._live[next(iter(self._live))]
        challenge = webauthn.helpers.generate_challenge()
        self._live[challenge] = time.monotonic() + self._ttl
        return challenge

    def consume(self, challenge: bytes) -> bool:
        """Whether `challenge` was live; either way it cannot be used again."""
        self._sweep()
        return self._live.pop(challenge, None) is not None

    def _sweep(self) -> None:
        now = time.monotonic()
        for challenge, expires in list(self._live.items()):
            if expires >= now:
                break
            del self._live[challenge]


challenges = ChallengeCache()


# region Ceremony


def registration_options(
    repo: PasskeyRepo, *, rp_id: str, rp_name: str, challenge: bytes
) -> PublicKeyCredentialCreationOptions:
    """What the browser needs to create a credential: exclude what is already registered."""
    existing = [PublicKeyCredentialDescriptor(id=p.credential_id) for p in repo.passkeys()]
    return webauthn.generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=repo.passkey_user_handle(),
        user_name="operator",
        challenge=challenge,
        attestation=AttestationConveyancePreference.NONE,
        exclude_credentials=existing,
    )


def client_data_challenge(credential: dict[str, Any]) -> bytes:
    """The challenge a `PublicKeyCredential` response carries, decoded from its client data."""
    client_data = json.loads(base64url_to_bytes(credential["response"]["clientDataJSON"]))
    return base64url_to_bytes(client_data["challenge"])


def verify_registration(
    repo: PasskeyRepo,
    credential: dict[str, Any],
    *,
    expected_challenge: bytes,
    rp_id: str,
    origin: str,
    label: str,
    now_ns: int,
) -> PasskeyRow:
    """Verify a `none`-attestation registration response and store the credential."""
    verified = webauthn.verify_registration_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=rp_id,
        expected_origin=origin,
    )
    transports = credential.get("response", {}).get("transports") or []
    aaguid = verified.aaguid.encode() if isinstance(verified.aaguid, str) else verified.aaguid
    return repo.add_passkey(
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        label=label,
        created_ns=now_ns,
        aaguid=aaguid or None,
        transports=transports,
    )


def login_options(
    repo: PasskeyRepo, *, rp_id: str, challenge: bytes
) -> PublicKeyCredentialRequestOptions:
    """What the browser needs to produce an assertion: any credential this runner holds."""
    allowed = [PublicKeyCredentialDescriptor(id=p.credential_id) for p in repo.passkeys()]
    return webauthn.generate_authentication_options(
        rp_id=rp_id, challenge=challenge, allow_credentials=allowed
    )


def verify_login(
    repo: PasskeyRepo,
    credential: dict[str, Any],
    *,
    expected_challenge: bytes,
    rp_id: str,
    origin: str,
) -> PasskeyRow:
    """Verify an assertion against the credential it claims, and bump its `sign_count`.

    Raises `ValueError` for a credential this runner does not hold; whatever
    `webauthn.verify_authentication_response` raises for anything else wrong
    with the assertion (bad signature, stale sign count, wrong origin, ...).
    """
    raw_id = credential.get("rawId") or credential["id"]
    credential_id = base64url_to_bytes(raw_id)
    row = repo.passkey(credential_id)
    if row is None:
        raise ValueError("no such passkey")
    verified = webauthn.verify_authentication_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=rp_id,
        expected_origin=origin,
        credential_public_key=row.public_key,
        credential_current_sign_count=row.sign_count,
    )
    repo.update_passkey_sign_count(credential_id, verified.new_sign_count)
    return row


# endregion
