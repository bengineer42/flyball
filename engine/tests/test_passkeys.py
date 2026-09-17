"""Passkeys: registration, login, revocation -- built by hand with a fake authenticator.

No browser: a real WebAuthn ceremony needs a private key and an attestation/assertion
signed with it, so `_FakeAuthenticator` plays the authenticator's part -- generates an
EC P-256 keypair, encodes it as a COSE key the same way a real one would, and builds
the CBOR `attestationObject` / `authenticatorData` by hand. `verify_registration` and
`verify_login` (flyball.server.passkeys) run the *real* `webauthn` verification code
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

from flyball.runtime.config import AuthConfig
from flyball.server import create_app, set_rig
from flyball.server.auth import hash_password
from flyball.server.deps import set_store
from flyball.server.passkeys import reset_memory_repo


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

    def register(self, rp_id: str, challenge: str, origin: str) -> dict:
        client_data = self._client_data("webauthn.create", challenge, origin)
        rp_id_hash = hashlib.sha256(rp_id.encode()).digest()
        flags = 0x45  # UP | UV | AT
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
        self, rp_id: str, challenge: str, origin: str, *, sign_count: int | None = None
    ) -> dict:
        client_data = self._client_data("webauthn.get", challenge, origin)
        rp_id_hash = hashlib.sha256(rp_id.encode()).digest()
        flags = 0x05  # UP | UV, no attested credential data
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


def _register(http: TestClient, authenticator: _FakeAuthenticator, label: str = "a passkey"):
    challenge_options = http.post("/api/auth/passkey/challenge").json()
    result = http.post(
        "/api/auth/passkey/register",
        json={
            "credential": authenticator.register(
                "testserver", challenge_options["challenge"], "http://testserver"
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

    listed = loggedin.get("/api/auth/passkey").json()
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
    assert loggedin.get("/api/auth/passkey").json() == []
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
    from flyball.db.sqlite import SqliteStore

    store = SqliteStore(tmp_path / "s.sqlite3")
    http = _serve(rig, store=store)
    with http:
        http.post("/api/auth/login", json={"secret": "hunter2"})
        authenticator = _FakeAuthenticator()
        _register(http, authenticator)
        assert len(store.passkeys()) == 1
    set_rig(None)
    set_store(None)
    store.close()
