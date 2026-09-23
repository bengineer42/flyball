"""Which verb each request needs: the one table every route has a row in.

A *verb* is what a principal's scopes (`scp`) grant on a rig. The vocabulary is pending
D-034; until then it is a two-verb placeholder, `read` and `operate`, that reproduces the
door's old rule (a GET or a stream needs read, anything else operate) plus the rows already
decided: the two check routes need read, stop needs operate, and each `/mcp/<mode>` needs a
verb of [MCP_MODES][flyball.interfaces.server.verbs.MCP_MODES] for its mode.

The D-034 round edits `VOCABULARY`, the verbs in `TABLE`, `MCP_MODES`, and the front's
`daemon/internal/grants/vocabulary.json` (whose vocabulary must equal `VOCABULARY`) -- data,
not code.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Final

from starlette.routing import compile_path

READ: Final = "read"
"""Every candidate vocabulary has it."""
OPERATE: Final = "operate"
"""The operator verb; stop needs it (decided). D-034 may rename the string."""
VOCABULARY: Final[frozenset[str]] = frozenset({READ, OPERATE})  # TODO(D-034)

# Behind the door; a GET or HEAD anywhere else is the bundled UI or the API's own
# description of itself (`/docs`, `/openapi.json`), and needs nothing.
GUARDED: Final = ("/api", "/ws", "/mcp")
# Reachable by anyone, whatever the method: the door itself.
OPEN: Final = ("/api/auth",)


@dataclass(frozen=True)
class Rule:
    method: str
    """`GET`, `POST`, `PUT`, `PATCH`, `DELETE`, or `WS` for a websocket. HEAD is GET."""
    path: str
    """In Starlette's path format, exactly as the route declares it, root path stripped."""
    verb: str | None
    """What the request needs; None: open (only `/api/auth` and its sub-routes)."""


MCP_MODES: Final[dict[str, frozenset[str]]] = {  # TODO(D-034)
    "read": frozenset({READ}),
    "author": frozenset({OPERATE}),
    "operate": frozenset({OPERATE}),
}
"""The verbs that admit a caller to `/mcp/<mode>`: any one of them."""

# One row per route and method, in the order the app matches them, so a literal path
# (`/api/controllers/schema`) comes before the pattern that would also match it.
TABLE: Final[tuple[Rule, ...]] = (  # TODO(D-034): the verbs; the rows are fixed by the routes
    Rule("GET", "/mcp/read", READ),
    Rule("POST", "/mcp/read", READ),
    Rule("DELETE", "/mcp/read", READ),
    Rule("GET", "/mcp/author", OPERATE),
    Rule("POST", "/mcp/author", OPERATE),
    Rule("DELETE", "/mcp/author", OPERATE),
    Rule("GET", "/mcp/operate", OPERATE),
    Rule("POST", "/mcp/operate", OPERATE),
    Rule("DELETE", "/mcp/operate", OPERATE),
    Rule("GET", "/api/controllers", READ),
    Rule("GET", "/api/controllers/schema", READ),
    Rule("GET", "/api/controllers/default", READ),
    Rule("GET", "/api/controllers/{address}", READ),
    Rule("POST", "/api/controllers", OPERATE),
    Rule("DELETE", "/api/controllers/{address}", OPERATE),
    Rule("POST", "/api/controllers/{address}/regulate", OPERATE),
    Rule("POST", "/api/controllers/{address}/manual", OPERATE),
    Rule("PUT", "/api/controllers/{address}/reference", OPERATE),
    Rule("GET", "/api/runner", READ),
    Rule("POST", "/api/runner/shutdown", OPERATE),
    Rule("POST", "/api/runner/restart", OPERATE),
    Rule("GET", "/api/health", READ),
    Rule("GET", "/api/clock", READ),
    Rule("GET", "/api/rig/schema", READ),
    Rule("GET", "/api/rig/config", READ),
    Rule("POST", "/api/rig/check", READ),
    Rule("GET", "/api/tunings", READ),
    Rule("GET", "/api/tunings/{tag}", READ),
    Rule("PUT", "/api/tunings/{tag}", OPERATE),
    Rule("GET", "/api/devices", READ),
    Rule("GET", "/api/devices/{name}", READ),
    Rule("GET", "/api/devices/{name}/schema", READ),
    Rule("POST", "/api/devices/{name}/restart", OPERATE),
    Rule("PUT", "/api/devices/{name}/demand", OPERATE),
    Rule("PUT", "/api/signals/{address}", OPERATE),
    Rule("POST", "/api/devices/{name}/commands/{tag}", OPERATE),
    Rule("POST", "/api/links", OPERATE),
    Rule("DELETE", "/api/links/{name}", OPERATE),
    Rule("POST", "/api/devices", OPERATE),
    Rule("DELETE", "/api/devices/{name}", OPERATE),
    Rule("POST", "/api/rig", OPERATE),
    Rule("GET", "/api/rig/document", READ),
    Rule("GET", "/api/rig/changes", READ),
    Rule("GET", "/api/rig/versions", READ),
    Rule("GET", "/api/rig/versions/{version_id}", READ),
    Rule("POST", "/api/rig/versions/{version_id}/restore", OPERATE),
    Rule("POST", "/api/rig/save", OPERATE),
    Rule("GET", "/api/drivers", READ),
    Rule("POST", "/api/drivers/reload", OPERATE),
    Rule("POST", "/api/probe", OPERATE),
    Rule("POST", "/api/links/{name}/query", OPERATE),
    Rule("GET", "/api/read", READ),
    Rule("GET", "/api/read/{address}", READ),
    Rule("GET", "/api/recording", READ),
    Rule("POST", "/api/recording", OPERATE),
    Rule("POST", "/api/recording/end", OPERATE),
    Rule("GET", "/api/waits", READ),
    Rule("GET", "/api/waits/{name}", READ),
    Rule("POST", "/api/waits/{name}/fire", OPERATE),
    Rule("POST", "/api/waits/{name}/interrupt", OPERATE),
    Rule("GET", "/api/events", READ),
    Rule("WS", "/ws/events", READ),
    Rule("GET", "/api/programs/schema", READ),
    Rule("GET", "/api/programs/commands", READ),
    Rule("POST", "/api/programs/check", READ),
    Rule("GET", "/api/programs/running", READ),
    Rule("POST", "/api/programs/run", OPERATE),
    Rule("POST", "/api/programs/command", OPERATE),
    Rule("POST", "/api/programs/interrupt", OPERATE),
    Rule("GET", "/api/schema", READ),
    Rule("GET", "/api/sim", READ),
    Rule("PUT", "/api/sim/clock", OPERATE),
    Rule("POST", "/api/sim/clock/step", OPERATE),
    Rule("GET", "/api/sim/plants/{name}", READ),
    Rule("PUT", "/api/sim/plants/{name}", OPERATE),
    Rule("POST", "/api/sim/plants/{name}/reset", OPERATE),
    Rule("GET", "/api/sim/config", READ),
    Rule("POST", "/api/sim/save", OPERATE),
    Rule("GET", "/api/sim/device", READ),
    Rule("GET", "/api/sim/device/schema", READ),
    Rule("POST", "/api/sim/device/{command}", OPERATE),
    Rule("GET", "/api/history/sessions", READ),
    Rule("GET", "/api/history/sessions/{session_id}", READ),
    Rule("POST", "/api/history/sessions/{session_id}/end", OPERATE),
    Rule("DELETE", "/api/history/sessions/{session_id}", OPERATE),
    Rule("PUT", "/api/history/sessions/{session_id}", OPERATE),
    Rule("PATCH", "/api/history/sessions/{session_id}", OPERATE),
    Rule("POST", "/api/history/sessions/{session_id}/keep", OPERATE),
    Rule("GET", "/api/history/sessions/{session_id}/documents", READ),
    Rule("GET", "/api/history/sessions/{session_id}/devices", READ),
    Rule("GET", "/api/history/sessions/{session_id}/signals", READ),
    Rule("GET", "/api/history/sessions/{session_id}/writes", READ),
    Rule("GET", "/api/history/sessions/{session_id}/controllers", READ),
    Rule("GET", "/api/history/sessions/{session_id}/series/{address}", READ),
    Rule("GET", "/api/history/sessions/{session_id}/writes/{address}", READ),
    Rule("GET", "/api/history/sessions/{session_id}/ticks/{controller}", READ),
    Rule("GET", "/api/history/sessions/{session_id}/events", READ),
    Rule("GET", "/api/history/sessions/{session_id}/spans", READ),
    Rule("GET", "/api/history/tunings", READ),
    Rule("GET", "/api/history/tunings/{name}", READ),
    Rule("GET", "/api/history/tunings/{name}/history", READ),
    Rule("PUT", "/api/history/tunings/{name}", OPERATE),
    Rule("DELETE", "/api/history/tunings/{name}", OPERATE),
    Rule("GET", "/api/history/sessions/{session_id}/export", READ),
    Rule("GET", "/api/history/sessions/{session_id}/series/{address}/export", READ),
    Rule("GET", "/api/history/sessions/{session_id}/writes/{address}/export", READ),
    Rule("GET", "/api/history/sessions/{session_id}/ticks/{controller}/export", READ),
    Rule("GET", "/api/history/sessions/{session_id}/events/export", READ),
    Rule("GET", "/api/dashboards/schema", READ),
    Rule("GET", "/api/dashboards/widgets", READ),
    Rule("GET", "/api/dashboards", READ),
    Rule("GET", "/api/dashboards/{name}", READ),
    Rule("GET", "/api/dashboards/{name}/history", READ),
    Rule("PUT", "/api/dashboards/{name}", OPERATE),
    Rule("POST", "/api/dashboards/{name}/rename", OPERATE),
    Rule("DELETE", "/api/dashboards/{name}", OPERATE),
    Rule("GET", "/api/programs/library", READ),
    Rule("POST", "/api/programs/library/import", OPERATE),
    Rule("GET", "/api/programs/library/formats", READ),
    Rule("GET", "/api/programs/library/{name}", READ),
    Rule("GET", "/api/programs/library/{name}/check", READ),
    Rule("GET", "/api/programs/library/{name}/history", READ),
    Rule("GET", "/api/programs/library/{name}/download", READ),
    Rule("PUT", "/api/programs/library/{name}", OPERATE),
    Rule("DELETE", "/api/programs/library/{name}", OPERATE),
    Rule("POST", "/api/programs/library/{name}/rename", OPERATE),
    Rule("POST", "/api/programs/library/{name}/run", OPERATE),
    Rule("WS", "/ws/samples", READ),
    Rule("WS", "/ws/controllers", READ),
    Rule("WS", "/ws/waits", READ),
    Rule("GET", "/api/auth", None),
    Rule("POST", "/api/auth/login", None),
    Rule("POST", "/api/auth/logout", None),
    Rule("GET", "/api/auth/link", None),
    Rule("POST", "/api/auth/link", None),
    Rule("GET", "/api/auth/front", None),
    Rule("POST", "/api/rig/stop", OPERATE),
)


class Unmapped(LookupError):
    """A request under `/api`, `/ws` or `/mcp` that no row covers: refused, never guessed."""


_MATCHERS: Final[tuple[tuple[str, re.Pattern[str], Rule], ...]] = tuple(
    (rule.method, compile_path(rule.path)[0], rule) for rule in TABLE
)


def needed(scope: Mapping[str, Any]) -> str | None:
    """The verb a request needs; None if it is open.

    A GET or HEAD outside `/api`, `/ws` and `/mcp` is the bundled UI (on a bare runner),
    open as before; any other request out there keeps the old rule (a websocket needs
    read, the rest operate), and so does an OPTIONS (a CORS preflight). Raises
    [Unmapped][flyball.interfaces.server.verbs.Unmapped] for a guarded request no row covers.
    """
    path = _path(scope)
    if _under(path, OPEN):
        return None
    method = "WS" if scope["type"] == "websocket" else str(scope.get("method", "GET"))
    method = "GET" if method == "HEAD" else method
    if not _under(path, GUARDED):
        if method == "GET":
            return None
        return READ if method == "WS" else OPERATE
    if method == "OPTIONS":
        return OPERATE
    for want, pattern, rule in _MATCHERS:
        if want == method and pattern.match(path):
            return rule.verb
    raise Unmapped(f"{method} {path}")


def allows(scp: Collection[str], scope: Mapping[str, Any]) -> bool:
    """Whether scopes `scp` admit the request; an unmapped one never."""
    try:
        verb = needed(scope)
    except Unmapped:
        return False
    if verb is None:
        return True
    path = _path(scope)
    mode = path.removeprefix("/mcp/") if path.startswith("/mcp/") else None
    if mode in MCP_MODES:
        return not MCP_MODES[mode].isdisjoint(scp)
    return verb in scp


def _path(scope: Mapping[str, Any]) -> str:
    path: str = scope["path"]
    root: str = scope.get("root_path") or ""
    return path[len(root) :] if root and path.startswith(root) else path


def _under(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)
