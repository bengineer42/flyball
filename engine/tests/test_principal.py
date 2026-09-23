"""The signed principal v1: the golden vectors the Go front shares, and the key file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flyball.interfaces.server.principal import (
    CODES,
    HEADER,
    Claims,
    Refused,
    mint,
    read_key_file,
    verify,
)

VECTORS = Path(__file__).parents[2] / "daemon/internal/principal/testdata/principal-v1.json"
DOC = json.loads(VECTORS.read_text(encoding="utf-8"))


def _claims(raw: dict) -> Claims:
    return Claims(
        sub=raw["sub"],
        sid=raw["sid"],
        scp=frozenset(raw["scp"]),
        kind=raw["kind"],
        aud=raw["aud"],
        cip=raw["cip"],
        sch=raw["sch"],
        iat=raw["iat"],
        exp=raw["exp"],
        nm=raw.get("nm", ""),
        via=raw.get("via", ""),
    )


def test_the_file_has_nineteen():
    assert len(DOC["vectors"]) == 19
    assert HEADER == "x-flyball-principal"


@pytest.mark.parametrize("vector", DOC["vectors"], ids=[v["name"] for v in DOC["vectors"]])
def test_principal_vectors(vector):
    key = bytes.fromhex(vector["key"])
    try:
        got = verify(vector["token"], key, DOC["aud_of_verifier"], now=vector["now"])
        result = "ok"
    except Refused as e:
        assert e.code in CODES
        result = e.code
    assert result == vector["expect"]
    if vector["mint"]:
        assert mint(key, _claims(vector["claims"])) == vector["token"]
        payload = vector["token"].split(".")[1]
        import base64

        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
        assert raw == vector["payload"]
    if result == "ok":
        assert got.aud == DOC["aud_of_verifier"] and isinstance(got.scp, frozenset)


@pytest.mark.parametrize("bad", ["a\x00b", "a b", "a b", "line\n"])
def test_mint_refuses_control_characters(bad):
    base = _claims(DOC["vectors"][0]["claims"])
    from dataclasses import replace

    with pytest.raises(ValueError):
        mint(bytes(32), replace(base, nm=bad))
    with pytest.raises(ValueError):
        mint(bytes(32), replace(base, scp=frozenset({bad})))


def test_verify_defaults_now_to_the_wall_clock():
    import time

    now = int(time.time())
    c = Claims("s", "i", frozenset({"read"}), "human", "a", "", "http", now, now + 60)
    assert verify(mint(bytes(32), c), bytes(32), "a") == c
    with pytest.raises(Refused) as e:
        verify(mint(bytes(32), c), bytes(32), "other")
    assert e.value.code == "aud"


def test_an_oversized_token_is_format():
    with pytest.raises(Refused) as e:
        verify("v1." + "a" * 5000 + ".b", bytes(32), "a", now=0)
    assert e.value.code == "format"


def test_read_key_file(tmp_path):
    good = "0a1b2c3d" * 8
    path = tmp_path / "key"
    path.write_text(good + "\n")
    assert read_key_file(path) == bytes.fromhex(good)
    path.write_text(good)
    assert read_key_file(path) == bytes.fromhex(good)
    for bad in (good[:-1] + "\n", good.upper() + "\n", good + "\n\n", good + "00\n", "zz" * 32):
        path.write_text(bad)
        with pytest.raises(ValueError):
            read_key_file(path)
