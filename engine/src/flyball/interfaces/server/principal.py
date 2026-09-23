"""The signed principal v1: who the front says is asking, and what they may do on this rig.

One header, `X-Flyball-Principal`, minted by the front with the per-runner key it wrote to
the runner's front-dir (and, for its own MCP calls, by the runner with the same key):

    token   = "v1." B64(payload) "." B64(HMAC-SHA256(key, ASCII("v1." B64(payload))))
    B64     = base64url without padding
    payload = UTF-8 JSON: sub, nm?, sid, scp, kind, aud, cip, sch, via?, iat, exp

`verify` checks, in this order, and refuses with the first failing code: `format`,
`version`, `mac` (over the received bytes, before any parsing, in constant time), `json`,
`claims`, `aud`, `lifetime` (`0 < exp - iat <= 120`), `expired` (`now > exp + 5`) and
`future` (`iat > now + 5`). Unknown claims are ignored. `mint` writes the claims in the fixed
order with no whitespace, so the Go front and this module produce the same bytes; the golden
vectors both check against are `daemon/internal/principal/testdata/principal-v1.json`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

HEADER: Final = "x-flyball-principal"
"""The header, as ASGI spells it (lower case)."""
ERROR_HEADER: Final = "X-Flyball-Principal-Error"
"""On a 401 for a bad principal: its refusal code, which the front turns into a 502."""
LIFETIME: Final = 60
"""Seconds a minted principal lives."""
MAX_LIFETIME: Final = 120
LEEWAY: Final = 5
MAX_BYTES: Final = 4096
KINDS: Final = frozenset({"human", "service", "agent"})
SCHEMES: Final = frozenset({"http", "https"})
CODES: Final = (
    "format",
    "version",
    "mac",
    "json",
    "claims",
    "aud",
    "lifetime",
    "expired",
    "future",
)
"""The refusal codes, in the order they are checked; Go's are the same strings."""

_B64 = re.compile(r"\A[A-Za-z0-9_-]+\Z")
_KEY = re.compile(r"\A[0-9a-f]{64}\n?\Z")
_FORBIDDEN = re.compile(r"[\x00-\x1f\u2028\u2029]")


@dataclass(frozen=True)
class Claims:
    sub: str
    """The identity, `<provider>:<id>` (`local:admin`, `token:ci`, `anon:`); never an email."""
    sid: str
    """One random id per session, token or anonymous request."""
    scp: frozenset[str]
    """The caller's verbs on this rig."""
    kind: str
    """`human`, `service` or `agent`."""
    aud: str
    """The runner it is for: the audience the front assigned at spawn."""
    cip: str
    """The client's address as the front saw it; empty when unknown."""
    sch: str
    """`http` or `https`: how the client reached the front."""
    iat: int
    exp: int
    nm: str = ""
    """A display name, for audit and the UI; never authorises."""
    via: str = ""
    """`mcp` when the runner minted it for an MCP tool's own call."""


class Refused(Exception):
    """A principal that does not verify; `code` is one of `CODES`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def read_key_file(path: Path) -> bytes:
    """The 32-byte key in `path`: 64 lower-case hex characters and at most one newline."""
    text = path.read_bytes().decode("ascii", errors="replace")
    if not _KEY.match(text):
        raise ValueError(f"{path}: not a key (64 lower-case hex characters and a newline)")
    return bytes.fromhex(text.strip())


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    if not _B64.match(text) or len(text) % 4 == 1:
        raise Refused("format")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(key: bytes, signing: str) -> bytes:
    return hmac.new(key, signing.encode("ascii"), hashlib.sha256).digest()


def mint(key: bytes, claims: Claims) -> str:
    """The token for `claims`; `iat` and `exp` are the caller's. Refuses control characters."""
    strings = [claims.sub, claims.nm, claims.sid, claims.kind, claims.aud, claims.cip]
    strings += [claims.sch, claims.via, *claims.scp]
    if any(_FORBIDDEN.search(s) for s in strings):
        raise ValueError("a claim holds a control character, U+2028 or U+2029")
    body: dict[str, Any] = {"sub": claims.sub}
    if claims.nm:
        body["nm"] = claims.nm
    body |= {
        "sid": claims.sid,
        "scp": sorted(claims.scp),
        "kind": claims.kind,
        "aud": claims.aud,
        "cip": claims.cip,
        "sch": claims.sch,
    }
    if claims.via:
        body["via"] = claims.via
    body |= {"iat": claims.iat, "exp": claims.exp}
    payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signing = "v1." + _b64(payload)
    return signing + "." + _b64(_sign(key, signing))


def verify(token: str, key: bytes, aud: str, now: int | None = None) -> Claims:
    """The claims `token` carries, if it is a v1 principal for `aud` signed with `key`.

    Raises [Refused][flyball.interfaces.server.principal.Refused] with the first check
    that fails. `now` is Unix seconds, the wall clock by default.
    """
    now = int(time.time()) if now is None else now
    if len(token.encode("utf-8", errors="replace")) > MAX_BYTES:
        raise Refused("format")
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise Refused("format")
    version, payload_b64, mac_b64 = parts
    payload, mac = _unb64(payload_b64), _unb64(mac_b64)
    if version != "v1":
        raise Refused("version")
    want = _sign(key, f"{version}.{payload_b64}")
    if len(mac) != 32 or not hmac.compare_digest(mac, want):
        raise Refused("mac")
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Refused("json") from None
    if not isinstance(raw, dict):
        raise Refused("json")
    claims = _claims(raw)
    if not hmac.compare_digest(claims.aud.encode(), aud.encode()):
        raise Refused("aud")
    if not 0 < claims.exp - claims.iat <= MAX_LIFETIME:
        raise Refused("lifetime")
    if now > claims.exp + LEEWAY:
        raise Refused("expired")
    if claims.iat > now + LEEWAY:
        raise Refused("future")
    return claims


def _claims(raw: dict[str, Any]) -> Claims:
    def text(name: str, *, optional: bool = False, empty: bool = False) -> str:
        value = raw.get(name)
        if value is None and optional:
            return ""
        if not isinstance(value, str) or (not empty and not value):
            raise Refused("claims")
        return value

    def number(name: str) -> int:
        value = raw.get(name)
        if type(value) is not int or not 0 <= value < 2**53:
            raise Refused("claims")
        return value

    scp = raw.get("scp")
    if not isinstance(scp, list) or not all(isinstance(s, str) and s for s in scp):
        raise Refused("claims")
    claims = Claims(
        sub=text("sub"),
        sid=text("sid"),
        scp=frozenset(scp),
        kind=text("kind"),
        aud=text("aud"),
        cip=text("cip", empty=True),
        sch=text("sch"),
        iat=number("iat"),
        exp=number("exp"),
        nm=text("nm", optional=True, empty=True),
        via=text("via", optional=True, empty=True),
    )
    if claims.kind not in KINDS or claims.sch not in SCHEMES:
        raise Refused("claims")
    return claims
