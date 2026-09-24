"""The verb table: which verb each route needs (the two-verb placeholder, pending D-034)."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.routing import BaseRoute, Mount, Route, WebSocketRoute

from flyball.interfaces.client import Rig as Client
from flyball.interfaces.mcp.http import mount
from flyball.interfaces.server import create_app, verbs
from flyball.interfaces.server.verbs import (
    MCP_MODES,
    OPERATE,
    READ,
    TABLE,
    VOCABULARY,
    Unmapped,
    allows,
    needed,
)

VOCABULARY_JSON = Path(__file__).parents[2] / "daemon/internal/grants/vocabulary.json"
GUARDED = ("/api", "/ws", "/mcp")
MCP_METHODS = ("GET", "POST", "DELETE")
# Rows whose verb is decided, not today's: everything else must match `_before()`.
DECIDED = {
    ("POST", "/api/rig/check"): READ,
    ("POST", "/api/programs/check"): READ,
    ("POST", "/api/rig/stop"): OPERATE,
    # Every `/mcp/<mode>` row, whatever the method, needs a verb of `MCP_MODES[mode]`.
    **{(m, "/mcp/read"): READ for m in MCP_METHODS},
    **{(m, f"/mcp/{mode}"): OPERATE for m in MCP_METHODS for mode in ("author", "operate")},
}


def _flat(routes: Iterable[BaseRoute], prefix: str = "") -> Iterator[tuple[str, BaseRoute]]:
    """Every route, with FastAPI's included routers opened up."""
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _flat(included.routes, prefix + route.include_context.prefix)  # type: ignore[attr-defined]
        else:
            yield prefix, route


def _app():
    """A fresh app with MCP mounted, as `_served()` walks by default."""
    app = create_app()
    mount(app, Client("http://127.0.0.1:1"), name="t")
    return app


def _served(app: Any = None) -> set[tuple[str, str]]:
    """(method, path) for every guarded route of an app with MCP mounted."""
    if app is None:
        app = _app()
    served: set[tuple[str, str]] = set()
    for prefix, route in _flat(app.routes):
        if isinstance(route, Mount):
            continue
        path = prefix + getattr(route, "path", "")
        if not path.startswith(GUARDED):
            continue
        if isinstance(route, WebSocketRoute):
            served.add(("WS", path))
        elif isinstance(route, Route):
            methods = route.methods or set(MCP_METHODS)
            served |= {(m, path) for m in methods if m != "HEAD"}
    return served


def _scope(method: str, path: str, root_path: str = "") -> dict[str, Any]:
    if method == "WS":
        return {"type": "websocket", "path": root_path + path, "root_path": root_path}
    return {"type": "http", "method": method, "path": root_path + path, "root_path": root_path}


def _concrete(path: str) -> str:
    """A path the row's pattern matches: each `{param}` filled in."""
    return "/".join("x" if part.startswith("{") else part for part in path.split("/"))


def test_vocabulary_matches_go():
    document = json.loads(VOCABULARY_JSON.read_text())
    assert sorted(VOCABULARY) == document["vocabulary"]
    assert document["pending"] == "D-034"


def test_every_route_has_a_row():
    rows = {(rule.method, rule.path) for rule in TABLE}
    assert _served() - rows == set()


def test_every_row_is_a_route():
    rows = {(rule.method, rule.path) for rule in TABLE}
    assert rows - _served() == set()


def test_every_route_has_a_row_bites():
    """Proof it bites: a route added with no `TABLE` row makes the walk fail."""
    app = _app()
    app.add_api_route("/api/no/such/dummy", lambda: None, methods=["GET"])
    rows = {(rule.method, rule.path) for rule in TABLE}
    missing = _served(app) - rows
    assert missing == {("GET", "/api/no/such/dummy")}


def _before(scope: dict[str, Any]) -> str:
    """The door's rule before the verb table (`auth.needed()` at `ac19996`), kept to compare."""
    path = scope["path"]
    if path == "/api/auth" or path.startswith("/api/auth/"):
        return "none"
    if scope["type"] == "websocket":
        return "read"
    if scope.get("method") in ("GET", "HEAD"):
        guarded = any(path == p or path.startswith(p + "/") for p in ("/api", "/ws", "/mcp"))
        return "read" if guarded else "none"
    return "operate"


def test_placeholder_matches_today():
    levels = {"none": None, "read": READ, "operate": OPERATE}
    for rule in TABLE:
        scope = _scope(rule.method, _concrete(rule.path))
        want = DECIDED.get((rule.method, rule.path), levels[_before(scope)])
        assert rule.verb == want, rule
        assert needed(scope) == want, rule


def test_decided_rows():
    assert needed(_scope("POST", "/api/rig/stop")) == OPERATE
    assert needed(_scope("POST", "/api/rig/check")) == READ
    assert needed(_scope("POST", "/api/programs/check")) == READ
    assert allows({READ}, _scope("POST", "/mcp/read"))
    assert not allows({READ}, _scope("POST", "/mcp/author"))
    assert not allows({READ}, _scope("POST", "/mcp/operate"))
    assert allows({OPERATE}, _scope("POST", "/mcp/operate"))
    assert set(MCP_MODES) == {"read", "author", "operate"}


def test_the_bundled_ui_is_open():
    assert needed(_scope("GET", "/")) is None
    assert needed(_scope("HEAD", "/assets/index.js")) is None
    assert needed(_scope("GET", "/api/auth")) is None


def test_head_is_get():
    assert needed(_scope("HEAD", "/api/devices")) == READ


def test_root_path_is_stripped():
    assert needed(_scope("POST", "/api/rig/stop", root_path="/rigs/a")) == OPERATE


def test_a_literal_row_wins_over_a_pattern():
    rows = [rule.path for rule in TABLE if rule.method == "GET"]
    assert rows.index("/api/controllers/schema") < rows.index("/api/controllers/{address}")


def test_unmapped_guarded_path_refused():
    scope = _scope("POST", "/api/no/such/route")
    with pytest.raises(Unmapped):
        needed(scope)
    assert not allows(VOCABULARY, scope)


def test_mcp_rows_follow_the_modes():
    for rule in TABLE:
        if rule.path.startswith("/mcp/"):
            assert rule.verb in MCP_MODES[rule.path.removeprefix("/mcp/")], rule


def test_verbs_are_in_the_vocabulary():
    assert {rule.verb for rule in TABLE} - {None} <= VOCABULARY
    assert all(modes <= VOCABULARY for modes in MCP_MODES.values())
    assert verbs.READ in VOCABULARY and verbs.OPERATE in VOCABULARY
