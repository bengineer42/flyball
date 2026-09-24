"""The action audit's middleware: one row per request that acts on the rig.

`Audit` sits outside the door, so it sees what the door decided. A request is recorded
when its verb (`verbs.needed`) is neither read nor open -- or it has no verb-table row, which
the door refuses -- and it came with a verified principal that is not anonymous (neither
the bare door's `anonymous` scheme nor the `anon:` subject, which is how the front's
visitor with no credential arrives). So a 403 for a principal lacking the verb is recorded,
`denied`; a request with no principal, a bad one or an anonymous one is not: it identifies
no one, and recording it would let anyone who reaches the runner fill its disk (the front
records authentication failures). An identified caller's denials are kept to
`DENIED_PER_MINUTE` rows a minute, each demand's to `DENIED_WRITES` addresses; what is
left out is counted in the log. A CORS preflight (`OPTIONS`) and a websocket (every one
needs only read) act on nothing and are not recorded either.

For a write (`PUT /api/signals/{address}`, `PUT /api/devices/{name}/write`) the row
carries each signal's old value (its last write, else its latest reading, as the rig had it
just before), the value requested (the body) and the value applied (the answer's, after the
rig's limits; None when refused). For a stop, the reason. The row is queued on
[AUDITOR][flyball.interfaces.server.audit.AUDITOR] when the response starts -- a demand's
once its body is complete -- so nothing here waits for the store, and nothing the audit does
refuses or delays the request.

The request id is the front's `X-Request-Id` when it is one (32 hex digits); otherwise,
bare or garbled, the runner makes one.
"""

from __future__ import annotations

import atexit
import json
import logging
import re
import secrets
import time
from collections import deque
from typing import Any, Final, cast

from fastapi import HTTPException
from starlette.routing import compile_path

from flyball.foundation.actor import Actor, Via
from flyball.foundation.device import Signal
from flyball.interfaces.server import verbs
from flyball.interfaces.server.deps import current_rig, get_store
from flyball.interfaces.server.principal import ANONYMOUS
from flyball.record.audit import Action, Auditor, Write, outcome
from flyball.record.store import Store

__all__ = ["AUDITOR", "Audit", "record_stop"]

log = logging.getLogger("flyball.audit")


def _store() -> Store | None:
    try:
        return get_store()
    except HTTPException:
        return None


AUDITOR: Final = Auditor(_store)
"""The process's one auditor: the server's rows and the break-glass stop's."""
atexit.register(AUDITOR.close, 2.0)

REQUEST_ID: Final = re.compile(r"\A[0-9a-f]{32}\Z")
"""The front's `X-Request-Id`: 16 random bytes, hex."""

_SIGNAL: Final = "/api/signals/{address}"
_WRITE: Final = "/api/devices/{name}/write"
_STOP: Final = "/api/rig/stop"
_BODIES: Final = (_SIGNAL, _WRITE, _STOP)
"""Routes whose request body the row needs: what was asked for."""
_ROUTES: Final = tuple((rule.method, compile_path(rule.path)[0], rule.path) for rule in verbs.TABLE)
_MAX_BODY: Final = 64 * 1024
"""A body longer than this is not kept for the row (the route still gets all of it)."""
DENIED_PER_MINUTE: Final = 10
"""Denied rows recorded a minute for one caller (`sub`); the rest are counted in the log."""
DENIED_WRITES: Final = 16
"""The addresses a denied demand's row keeps of those it asked for."""
_DENIED_CALLERS: Final = 1024
"""Callers whose recent denials are remembered before the idle ones are forgotten."""


def _route(method: str, path: str) -> tuple[str, dict[str, str]]:
    """The route's template and parameters; the path itself when no row matches."""
    for want, pattern, template in _ROUTES:
        if want == method and (match := pattern.match(path)) is not None:
            return template, match.groupdict()
    return path, {}


def _path(scope: Any) -> str:
    path: str = scope["path"]
    root: str = scope.get("root_path") or ""
    return path[len(root) :] if root and path.startswith(root) else path


def _audited(scope: Any) -> bool:
    if scope["type"] != "http" or scope.get("method") == "OPTIONS":
        return False
    try:
        verb = verbs.needed(scope)
    except verbs.Unmapped:
        return True  # refused by the door, and still somebody's attempt to act
    return verb is not None and verb != verbs.READ


class Audit:
    """ASGI middleware: records each acting request on `auditor` (the module's description)."""

    def __init__(self, app: Any, auditor: Auditor = AUDITOR) -> None:
        self.app = app
        self.auditor = auditor
        self._denied: dict[str, deque[float]] = {}
        self._left_out: dict[str, int] = {}

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if not _audited(scope):
            await self.app(scope, receive, send)
            return
        started = time.time_ns()
        method = str(scope["method"])
        path = _path(scope)
        route, params = _route(method, path)
        body = b""
        if route in _BODIES:
            body, receive = await _buffered(receive)
        asked = _asked(route, params, body)
        old = _old(asked)
        answer = bytearray()
        status: int | None = None
        done = False

        def finish(code: int) -> None:
            nonlocal done
            if done:
                return
            done = True
            self._record(scope, started, method, route, path, code, asked, old, bytes(answer), body)

        async def sending(message: Any) -> None:
            nonlocal status
            try:
                if message["type"] == "http.response.start":
                    status = int(message["status"])
                    if route not in (_SIGNAL, _WRITE):
                        finish(status)
                elif message["type"] == "http.response.body" and status is not None and not done:
                    if len(answer) < _MAX_BODY:
                        answer.extend(message.get("body", b""))
                    if not message.get("more_body", False):
                        finish(status)
            except Exception:  # the audit never gets between the rig and its caller
                log.exception("audit: recording the response failed")
            await send(message)

        try:
            await self.app(scope, receive, sending)
        except BaseException:
            if not done:
                _safely(finish, 500 if status is None else status)
            raise
        if not done:
            _safely(finish, 500 if status is None else status)

    def _record(
        self,
        scope: Any,
        started: int,
        method: str,
        route: str,
        path: str,
        status: int,
        asked: dict[str, tuple[Signal | None, float | None]],
        old: dict[str, float | None],
        answer: bytes,
        body: bytes,
    ) -> None:
        state = scope.get("state") or {}
        claims = state.get("principal")
        scheme = str(state.get("scheme", ""))
        if claims is None or scheme == "anonymous" or claims.sub == ANONYMOUS:
            return
        denied = outcome(status) == "denied"
        if denied and not self._denial_kept(claims.sub):
            return
        if denied:
            asked = dict(list(asked.items())[:DENIED_WRITES])
        writes: dict[str, Write] | None = None
        if asked:
            applied = _applied(answer) if status < 300 else {}
            writes = {
                address: {
                    "old": old.get(address),
                    "requested": value,
                    "applied": applied.get(address),
                }
                for address, (_, value) in asked.items()
            }
        details = {"reason": _reason(body)} if route == _STOP else None
        self.auditor.record(
            Action(
                time_utc_ns=started,
                actor=Actor(
                    principal=claims.sub,
                    kind=claims.kind,
                    via=cast("Via", claims.via or "http"),
                    sid=claims.sid,
                ),
                name=claims.nm,
                cip=claims.cip,
                scheme=scheme,
                method=method,
                route=route,
                path=path,
                status=status,
                outcome=outcome(status),
                request_id=_request_id(scope),
                writes=writes,
                details=details,
            )
        )

    def _denial_kept(self, sub: str) -> bool:
        """Whether a denial of `sub`'s gets a row: `DENIED_PER_MINUTE` a minute do."""
        now = time.monotonic()
        if len(self._denied) > _DENIED_CALLERS:
            for idle in [k for k, v in self._denied.items() if not v or now - v[-1] > 60]:
                del self._denied[idle]
        recent = self._denied.setdefault(sub, deque())
        while recent and now - recent[0] > 60:
            recent.popleft()
        if len(recent) >= DENIED_PER_MINUTE:
            if sub not in self._left_out:
                log.warning(
                    "audit: %r was denied more than %d times in a minute;"
                    " its denials are counted, not recorded, until it slows",
                    sub,
                    DENIED_PER_MINUTE,
                )
            self._left_out[sub] = self._left_out.get(sub, 0) + 1
            return False
        if left_out := self._left_out.pop(sub, 0):
            log.warning("audit: %d denials of %r were not recorded", left_out, sub)
        recent.append(now)
        return True


def _safely(finish: Any, status: int) -> None:
    try:
        finish(status)
    except Exception:
        log.exception("audit: recording the request failed")


def _request_id(scope: Any) -> str:
    for name, value in scope.get("headers") or []:
        if name == b"x-request-id":
            given = value.decode("latin-1")
            if REQUEST_ID.match(given):
                return given
            break
    return secrets.token_hex(16)


async def _buffered(receive: Any) -> tuple[bytes, Any]:
    """The request's whole body, and a `receive` that hands the route the same messages."""
    messages: list[Any] = []
    body = bytearray()
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            break
        body.extend(message.get("body", b""))
        if not message.get("more_body", False):
            break

    async def replay() -> Any:
        if messages:
            return messages.pop(0)
        return await receive()

    return (bytes(body) if len(body) <= _MAX_BODY else b""), replay


def _asked(
    route: str, params: dict[str, str], body: bytes
) -> dict[str, tuple[Signal | None, float | None]]:
    """For a demand: by address, the signal (when the rig knows it) and the value asked for."""
    if route not in (_SIGNAL, _WRITE):
        return {}
    try:
        document = json.loads(body) if body else None
    except ValueError:
        return {}
    rig = current_rig()
    asked: dict[str, tuple[Signal | None, float | None]] = {}
    if route == _SIGNAL:
        address = params.get("address", "")
        signal: Signal | None = None
        if rig is not None:
            try:
                found = rig.resolve(address)
                signal = found if isinstance(found, Signal) else None
            except Exception:
                signal = None
        asked[address] = (signal, _number(document))
        return asked
    if not isinstance(document, dict):
        return {}
    name = params.get("name", "")
    device = None if rig is None else rig.devices.get(name)
    for key, value in document.items():
        signal = None
        if device is not None:
            try:
                found = device.root.find(str(key))
                signal = found if isinstance(found, Signal) else None
            except Exception:
                signal = None
        address = signal.address if signal is not None else f"{name}.{key}"
        asked[address] = (signal, _number(value))
    return asked


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _old(asked: dict[str, tuple[Signal | None, float | None]]) -> dict[str, float | None]:
    """Each signal's value just before: its last write, else its latest reading.

    Read without the rig's lock, on the loop: one dict lookup each, no store, no hardware.
    """
    rig = current_rig()
    old: dict[str, float | None] = {}
    if rig is None:
        return old
    for address, (signal, _) in asked.items():
        if signal is None:
            continue
        try:
            written = signal.device.written.get(signal)
            if written is not None and written.value is not None:
                old[address] = float(written.value)
                continue
            reading = rig.latest.get(signal)
            old[address] = None if reading is None else _number(reading.value)
        except Exception:
            old[address] = None
    return old


def _applied(answer: bytes) -> dict[str, float | None]:
    """The demand's answer, `{address: {value, ...}}`, as `{address: value}`."""
    try:
        document = json.loads(answer) if answer else None
    except ValueError:
        return {}
    if not isinstance(document, dict):
        return {}
    return {
        str(address): _number(state.get("value")) if isinstance(state, dict) else None
        for address, state in document.items()
    }


def _reason(body: bytes) -> str:
    try:
        document = json.loads(body) if body else None
    except ValueError:
        return ""
    reason = document.get("reason") if isinstance(document, dict) else None
    return reason[:500] if isinstance(reason, str) else ""


def record_stop(
    actor: Actor, reason: str, at_utc_ns: int, *, done: bool = True, auditor: Auditor = AUDITOR
) -> None:
    """Record a stop that came by no request: the break-glass signal's. Never raises.

    `reason` is the signal's name (`SIGUSR1`), the row's route; `done` False for a stop
    that raised.
    """
    try:
        auditor.record(
            Action(
                time_utc_ns=at_utc_ns,
                actor=actor,
                cip="",
                method="SIGNAL",
                route=reason,
                path="",
                status=None,
                outcome="done" if done else "failed",
                details={"reason": reason, "message": actor.message},
            )
        )
    except Exception:
        log.exception("audit: recording the stop failed")
