"""Passkeys: registration, login, revocation -- built by hand with a fake authenticator.

No browser: a real WebAuthn ceremony needs a private key and an attestation/assertion
signed with it, so `_FakeAuthenticator` plays the authenticator's part -- generates an
EC P-256 keypair, encodes it as a COSE key the same way a real one would, and builds
the CBOR `attestationObject` / `authenticatorData` by hand. `verify_registration` and
`verify_login` (flyball.interfaces.server.passkeys) run the *real* `webauthn` verification code
against it, so this exercises the same signature checks a browser's response would hit.
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256
from fastapi.testclient import TestClient
from webauthn.helpers import bytes_to_base64url, encode_cbor

from conftest import TestClient as LoopbackClient
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.auth import hash_password
from flyball.interfaces.server.deps import set_store
from flyball.interfaces.server.passkeys import reset_memory_repo
from flyball.runtime.config import AuthConfig


class _FakeAuthenticator:
    """One passkey: a P-256 keypair and the CBOR plumbing to register and assert with it."""

    def __init__(self) -> None:
        self.credential_id = os.urandom(32)
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.sign_count = 0

    def _cose_public_key(self) -> bytes:
        numbers = self.private_key.public_key().public_numbers()
        return encode_cbor({
            1: 2,  # kty: EC2
            3: -7,  # alg: ES256
            -1: 1,  # crv: P-256
            -2: numbers.x.to_bytes(32, "big"),
            -3: numbers.y.to_bytes(32, "big"),
        })

    def _client_data(self, kind: str, challenge: str, origin: str) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False},
            separators=(",", ":"),
        ).encode()

    def register(
        self, rp_id: str, challenge: str, origin: str, *, user_verified: bool = True
    ) -> dict:
        client_data = self._client_data("webauthn.create", challenge, origin)
        rp_id_hash = hashlib.sha256(rp_id.encode()).digest()
        flags = 0x45 if user_verified else 0x41  # UP | UV | AT, or a key with no PIN: UP | AT
        auth_data = (
            rp_id_hash
            + bytes([flags])
            + self.sign_count.to_bytes(4, "big")
            + bytes(16)  # aaguid: zero, this fake claims none
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + self._cose_public_key()
        )
        attestation_object = encode_cbor({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "attestationObject": bytes_to_base64url(attestation_object),
                "transports": ["internal"],
            },
        }

    def assertion(
        self,
        rp_id: str,
        challenge: str,
        origin: str,
        *,
        sign_count: int | None = None,
        user_verified: bool = True,
    ) -> dict:
        client_data = self._client_data("webauthn.get", challenge, origin)
        rp_id_hash = hashlib.sha256(rp_id.encode()).digest()
        flags = 0x05 if user_verified else 0x01  # UP | UV, or user presence alone
        count = self.sign_count + 1 if sign_count is None else sign_count
        auth_data = rp_id_hash + bytes([flags]) + count.to_bytes(4, "big")
        signature = self.private_key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(SHA256())
        )
        self.sign_count = count
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
            },
        }


def _serve(rig, *, store=None):
    rig.name = "t"
    set_rig(rig)
    set_store(store)
    reset_memory_repo()
    auth = AuthConfig(password=hash_password("hunter2"))
    app = create_app(auth, secret=b"k", login_delay=0)
    http = TestClient(app)
    return http


@pytest.fixture
def loggedin(rig):
    http = _serve(rig)
    with http:
        assert http.post("/api/auth/login", json={"secret": "hunter2"}).status_code == 200
        yield http
    set_rig(None)
    set_store(None)


def _register(
    http: TestClient,
    authenticator: _FakeAuthenticator,
    label: str = "a passkey",
    *,
    user_verified: bool = True,
):
    challenge_options = http.post("/api/auth/passkey/challenge").json()
    result = http.post(
        "/api/auth/passkey/register",
        json={
            "credential": authenticator.register(
                "testserver",
                challenge_options["challenge"],
                "http://testserver",
                user_verified=user_verified,
            ),
            "label": label,
        },
    )
    return result


def test_registering_and_listing_a_passkey(loggedin):
    authenticator = _FakeAuthenticator()
    result = _register(loggedin, authenticator, label="Ben's laptop")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["label"] == "Ben's laptop"
    assert body["transports"] == ["internal"]

    listing = loggedin.get("/api/auth/passkey").json()
    assert listing["store_backed"] is False
    listed = listing["passkeys"]
    assert len(listed) == 1 and listed[0]["id"] == body["id"]
    # never the public key or the credential id
    assert "public_key" not in listed[0] and "credential_id" not in listed[0]


def test_registration_requires_a_session(rig):
    http = _serve(rig)
    with http:
        result = http.post("/api/auth/passkey/challenge")
        assert result.status_code == 401
    set_rig(None)
    set_store(None)


def test_login_with_a_passkey_grants_operate_and_the_passkey_scheme(loggedin):
    authenticator = _FakeAuthenticator()
    assert _register(loggedin, authenticator).status_code == 200
    loggedin.post("/api/auth/logout")
    assert loggedin.get("/api/health").status_code == 401

    challenge = loggedin.post("/api/auth/passkey/login/challenge").json()
    response = loggedin.post(
        "/api/auth/passkey/login",
        json={
            "credential": authenticator.assertion(
                "testserver", challenge["challenge"], "http://testserver"
            )
        },
    )
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["scheme"] == "passkey" and out["level"] == "operate"
    assert loggedin.get("/api/health").status_code == 200
    assert loggedin.get("/api/auth").json()["scheme"] == "passkey"


def test_a_wrong_signature_is_refused(loggedin):
    authenticator = _FakeAuthenticator()
    assert _register(loggedin, authenticator).status_code == 200
    challenge = loggedin.post("/api/auth/passkey/login/challenge").json()
    forged = authenticator.assertion("testserver", challenge["challenge"], "http://testserver")
    # a different key signs it: same credential id, wrong signature
    authenticator.private_key = ec.generate_private_key(ec.SECP256R1())
    forged2 = authenticator.assertion("testserver", challenge["challenge"], "http://testserver")
    forged["response"]["signature"] = forged2["response"]["signature"]
    response = loggedin.post("/api/auth/passkey/login", json={"credential": forged})
    assert response.status_code == 401


def test_a_replayed_challenge_is_refused(loggedin):
    authenticator = _FakeAuthenticator()
    assert _register(loggedin, authenticator).status_code == 200
    challenge = loggedin.post("/api/auth/passkey/login/challenge").json()
    credential = authenticator.assertion("testserver", challenge["challenge"], "http://testserver")
    first = loggedin.post("/api/auth/passkey/login", json={"credential": credential})
    assert first.status_code == 200
    authenticator.sign_count -= 1  # rebuild the same assertion bytes, same challenge
    replay = loggedin.post("/api/auth/passkey/login", json={"credential": credential})
    assert replay.status_code == 400


def test_sign_count_advances_on_each_login(loggedin):
    authenticator = _FakeAuthenticator()
    _register(loggedin, authenticator)
    for _ in range(3):
        challenge = loggedin.post("/api/auth/passkey/login/challenge").json()
        credential = authenticator.assertion(
            "testserver", challenge["challenge"], "http://testserver"
        )
        response = loggedin.post("/api/auth/passkey/login", json={"credential": credential})
        assert response.status_code == 200
    assert authenticator.sign_count == 3


def test_a_cloned_authenticator_is_caught_by_a_stale_sign_count(loggedin):
    authenticator = _FakeAuthenticator()
    _register(loggedin, authenticator)
    challenge = loggedin.post("/api/auth/passkey/login/challenge").json()
    good = loggedin.post(
        "/api/auth/passkey/login",
        json={
            "credential": authenticator.assertion(
                "testserver", challenge["challenge"], "http://testserver"
            )
        },
    )
    assert good.status_code == 200
    # a clone replaying from an earlier counter value
    challenge2 = loggedin.post("/api/auth/passkey/login/challenge").json()
    stale = authenticator.assertion(
        "testserver", challenge2["challenge"], "http://testserver", sign_count=1
    )
    response = loggedin.post("/api/auth/passkey/login", json={"credential": stale})
    assert response.status_code == 401


def test_revoking_a_passkey(loggedin):
    authenticator = _FakeAuthenticator()
    body = _register(loggedin, authenticator).json()
    assert loggedin.delete(f"/api/auth/passkey/{body['id']}").status_code == 200
    assert loggedin.get("/api/auth/passkey").json()["passkeys"] == []
    assert loggedin.delete(f"/api/auth/passkey/{body['id']}").status_code == 404

    loggedin.post("/api/auth/logout")
    challenge = loggedin.post("/api/auth/passkey/login/challenge").json()
    response = loggedin.post(
        "/api/auth/passkey/login",
        json={
            "credential": authenticator.assertion(
                "testserver", challenge["challenge"], "http://testserver"
            )
        },
    )
    assert response.status_code == 401, "the credential was revoked"


def test_any_registered_passkey_grants_the_same_level(loggedin):
    a, b = _FakeAuthenticator(), _FakeAuthenticator()
    _register(loggedin, a, label="a")
    _register(loggedin, b, label="b")
    loggedin.post("/api/auth/logout")
    for authenticator in (a, b):
        challenge = loggedin.post("/api/auth/passkey/login/challenge").json()
        response = loggedin.post(
            "/api/auth/passkey/login",
            json={
                "credential": authenticator.assertion(
                    "testserver", challenge["challenge"], "http://testserver"
                )
            },
        )
        assert response.status_code == 200 and response.json()["level"] == "operate"
        loggedin.post("/api/auth/logout")


def test_passkeys_persist_in_a_store_but_not_without_one(rig, tmp_path):
    from flyball.record.sqlite import SqliteStore

    store = SqliteStore(tmp_path / "s.sqlite3")
    http = _serve(rig, store=store)
    with http:
        http.post("/api/auth/login", json={"secret": "hunter2"})
        authenticator = _FakeAuthenticator()
        _register(http, authenticator)
        assert len(store.passkeys()) == 1
        assert http.get("/api/auth/passkey").json()["store_backed"] is True
    set_rig(None)
    set_store(None)
    store.close()


def test_an_open_runner_takes_no_passkeys(rig):
    """No password, no token: nothing a passkey could open, so registering one is refused.

    Otherwise anyone passing an open runner could register a credential that
    would still open the door after a password is set.
    """
    rig.name = "t"
    set_rig(rig)
    set_store(None)
    reset_memory_repo()
    # an open runner answers loopback names only, and the stock client says `testserver`
    http = LoopbackClient(create_app(None, secret=b"k", login_delay=0))
    with http:
        assert http.get("/api/auth").json()["passkey"] is False
        assert http.post("/api/auth/passkey/challenge").status_code == 409
        assert http.post("/api/auth/passkey/login/challenge").status_code == 409
        assert http.get("/api/auth/passkey").status_code == 409
    set_rig(None)
    set_store(None)


def test_revoking_a_passkey_ends_its_sessions(loggedin):
    """A passkey session names its credential; once that is gone, so is the session."""
    authenticator = _FakeAuthenticator()
    body = _register(loggedin, authenticator).json()
    loggedin.post("/api/auth/logout")
    challenge = loggedin.post("/api/auth/passkey/login/challenge").json()
    signed_in = loggedin.post(
        "/api/auth/passkey/login",
        json={
            "credential": authenticator.assertion(
                "testserver", challenge["challenge"], "http://testserver"
            )
        },
    )
    assert signed_in.status_code == 200
    assert loggedin.get("/api/health").status_code == 200

    assert loggedin.delete(f"/api/auth/passkey/{body['id']}").status_code == 200
    assert loggedin.get("/api/health").status_code == 401, "the session died with the credential"
    assert loggedin.get("/api/auth").json()["scheme"] == "anonymous"


def test_a_password_session_is_not_bound_to_any_passkey(loggedin):
    authenticator = _FakeAuthenticator()
    body = _register(loggedin, authenticator).json()
    assert loggedin.delete(f"/api/auth/passkey/{body['id']}").status_code == 200
    assert loggedin.get("/api/health").status_code == 200, "signed in by password, unaffected"


def test_the_login_challenge_is_rate_limited_with_the_password_login(loggedin):
    loggedin.post("/api/auth/logout")
    for _ in range(10):
        assert loggedin.post("/api/auth/login", json={"secret": "wrong"}).status_code == 401
    assert loggedin.post("/api/auth/login", json={"secret": "wrong"}).status_code == 429
    assert loggedin.post("/api/auth/passkey/login/challenge").status_code == 429


def test_the_challenge_cache_is_capped():
    from flyball.interfaces.server.passkeys import ChallengeCache

    cache = ChallengeCache(ttl=300, cap=3)
    first = cache.issue()
    cache.issue()
    cache.issue()
    assert cache.consume(first), "within the cap, the oldest is still live"
    first = cache.issue()
    cache.issue()
    cache.issue()
    cache.issue()  # one over: the oldest goes
    assert not cache.consume(first)


def test_a_key_that_does_not_verify_the_user_cannot_register(loggedin):
    """A PIN-less authenticator is refused: a passkey grants `operate`."""
    authenticator = _FakeAuthenticator()
    result = _register(loggedin, authenticator, user_verified=False)
    assert result.status_code == 400
    assert "did not verify" in result.json()["detail"]
    assert loggedin.get("/api/auth/passkey").json()["passkeys"] == []


def test_an_assertion_without_user_verification_is_refused(loggedin):
    """Registered with a PIN, asserted without one: still no."""
    authenticator = _FakeAuthenticator()
    assert _register(loggedin, authenticator).status_code == 200
    loggedin.post("/api/auth/logout")
    challenge = loggedin.post("/api/auth/passkey/login/challenge").json()
    response = loggedin.post(
        "/api/auth/passkey/login",
        json={
            "credential": authenticator.assertion(
                "testserver", challenge["challenge"], "http://testserver", user_verified=False
            )
        },
    )
    assert response.status_code == 401
    assert loggedin.get("/api/health").status_code == 401


def test_both_ceremonies_ask_the_authenticator_for_user_verification(loggedin):
    """Not just checked after the fact -- the browser is told to require it."""
    registration = loggedin.post("/api/auth/passkey/challenge").json()
    assert registration["authenticatorSelection"]["userVerification"] == "required"
    login = loggedin.post("/api/auth/passkey/login/challenge").json()
    assert login["userVerification"] == "required"


def test_without_the_passkeys_extra_the_routes_say_so(loggedin, monkeypatch):
    """`webauthn` is the optional `passkeys` extra: no crash, no silent no-op, a 501."""
    from flyball.interfaces.server import passkeys

    monkeypatch.setattr(passkeys, "AVAILABLE", False)
    assert loggedin.get("/api/auth").json()["passkey"] is False, "the UI hides the button"
    assert loggedin.post("/api/auth/passkey/challenge").status_code == 501
    assert loggedin.post("/api/auth/passkey/login/challenge").status_code == 501
    assert loggedin.get("/api/auth/passkey").status_code == 501
    assert loggedin.get("/api/health").status_code == 200, "password login is untouched"


def test_a_store_that_cannot_be_asked_refuses_the_session_instead_of_failing(loggedin, monkeypatch):
    """The per-request credential check runs inside the door: a store error is a 401, not a 500."""
    from flyball.interfaces.server import passkeys

    authenticator = _FakeAuthenticator()
    assert _register(loggedin, authenticator).status_code == 200
    loggedin.post("/api/auth/logout")
    challenge = loggedin.post("/api/auth/passkey/login/challenge").json()
    signed_in = loggedin.post(
        "/api/auth/passkey/login",
        json={
            "credential": authenticator.assertion(
                "testserver", challenge["challenge"], "http://testserver"
            )
        },
    )
    assert signed_in.status_code == 200
    assert loggedin.get("/api/health").status_code == 200

    def boom(store):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(passkeys, "repo_for", boom)
    assert loggedin.get("/api/health").status_code == 401, "refused, not a 500"
    assert loggedin.get("/api/auth").json()["scheme"] == "anonymous"

    # and a password session never reaches that check, so it is unaffected
    assert loggedin.post("/api/auth/login", json={"secret": "hunter2"}).status_code == 200
    assert loggedin.get("/api/health").status_code == 200


@pytest.mark.parametrize(
    "credential",
    [
        {"response": {"clientDataJSON": "W10"}},  # []
        {"response": {"clientDataJSON": "MQ"}},  # 1
        {"response": {"clientDataJSON": "Im5vIg"}},  # "no"
        {"response": {"clientDataJSON": "bnVsbA"}},  # null
        {"response": "x"},
    ],
)
def test_malformed_client_data_is_a_400_not_a_500(loggedin, credential):
    """Client data that decodes to something other than an object is the caller's fault."""
    loggedin.post("/api/auth/logout")
    assert (
        loggedin.post("/api/auth/passkey/login", json={"credential": credential}).status_code == 400
    )


def test_malformed_client_data_counts_against_the_limiter(loggedin):
    """Before the fix these 500'd before `failure` was recorded, so they were unlimited."""
    loggedin.post("/api/auth/logout")
    bad = {"credential": {"response": {"clientDataJSON": "W10"}}}
    for _ in range(10):
        assert loggedin.post("/api/auth/passkey/login", json=bad).status_code == 400
    assert loggedin.post("/api/auth/passkey/login", json=bad).status_code == 429


def test_a_store_backed_passkey_session_never_takes_the_store_on_the_event_loop(rig, tmp_path):
    """Register, sign in, use the session, revoke: all against sqlite, all off the loop.

    The conftest guard fails this test if the app's loop took the store's lock
    (D-029): the door's per-request credential check and the login's sign-count
    bump both reach the store.
    """
    from flyball.record.sqlite import SqliteStore

    store = SqliteStore(tmp_path / "s.sqlite3")
    http = _serve(rig, store=store)
    try:
        with http:
            http.post("/api/auth/login", json={"secret": "hunter2"})
            authenticator = _FakeAuthenticator()
            passkey_id = _register(http, authenticator).json()["id"]
            http.post("/api/auth/logout")
            challenge = http.post("/api/auth/passkey/login/challenge").json()
            credential = authenticator.assertion(
                "testserver", challenge["challenge"], "http://testserver"
            )
            signed_in = http.post("/api/auth/passkey/login", json={"credential": credential})
            assert signed_in.status_code == 200, signed_in.text
            assert http.get("/api/auth").json()["scheme"] == "passkey"
            assert http.get("/api/health").status_code == 200
            assert http.delete(f"/api/auth/passkey/{passkey_id}").status_code == 200
            assert http.get("/api/health").status_code == 401, "revoked, so the session ended"
    finally:
        set_rig(None)
        set_store(None)
        store.close()


def test_a_passkey_post_from_another_site_is_refused_at_the_door(loggedin):
    """The passkey routes are under the open `/api/auth`, but the Origin check still applies."""
    evil = {"Origin": "https://evil.example"}
    assert loggedin.post("/api/auth/passkey/challenge", headers=evil).status_code == 403
    assert loggedin.post("/api/auth/passkey/login/challenge", headers=evil).status_code == 403
    assert loggedin.post("/api/auth/passkey/login", json={}, headers=evil).status_code == 403
    assert loggedin.delete("/api/auth/passkey/1", headers=evil).status_code == 403
    own = {"Origin": "http://testserver"}
    assert loggedin.post("/api/auth/passkey/challenge", headers=own).status_code == 200


def test_a_store_outage_while_registering_is_a_503_not_a_bad_credential(loggedin, monkeypatch):
    """D-027: a store that cannot be reached is the runner's trouble, not the caller's."""
    from flyball.interfaces.server import passkeys
    from flyball.record.errors import StoreUnavailableError

    def unavailable(*args, **kwargs):
        raise StoreUnavailableError("database is locked", "s.sqlite3")

    monkeypatch.setattr(passkeys.MemoryPasskeyRepo, "add_passkey", unavailable)
    assert _register(loggedin, _FakeAuthenticator()).status_code == 503


def test_a_store_outage_while_signing_in_is_a_503_and_not_a_failed_attempt(loggedin, monkeypatch):
    from flyball.interfaces.server import passkeys
    from flyball.record.errors import StoreUnavailableError

    authenticator = _FakeAuthenticator()
    assert _register(loggedin, authenticator).status_code == 200
    loggedin.post("/api/auth/logout")

    def unavailable(*args, **kwargs):
        raise StoreUnavailableError("database is locked", "s.sqlite3")

    monkeypatch.setattr(passkeys.MemoryPasskeyRepo, "update_passkey_sign_count", unavailable)
    for _ in range(11):
        challenge = loggedin.post("/api/auth/passkey/login/challenge")
        assert challenge.status_code == 200, "an outage is not counted against the address"
        credential = authenticator.assertion(
            "testserver", challenge.json()["challenge"], "http://testserver"
        )
        assert (
            loggedin.post("/api/auth/passkey/login", json={"credential": credential}).status_code
            == 503
        )
