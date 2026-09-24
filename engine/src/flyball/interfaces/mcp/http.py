"""The same servers over streamable HTTP, mounted in the runner at `/mcp/<tier>`.

A client then needs a URL and nothing installed:

    claude mcp add --transport http rig http://pi:8000/mcp/author

The tools still go through `flyball.interfaces.client`, so the runner hands `mount` a
client pointed back at itself, and a `sign` that makes each of a tool's calls carry the
caller: a principal minted for that one request, with the caller's identity and the verbs
it holds that the tier allows (the runner's `serving.mcp_signer`). Listing the tools and
refreshing the schema are the runner's own reads. Tools that run code on this machine
(`check_driver`, `search_drivers`) are not served here: the stdio server keeps them.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable
from functools import partial
from typing import Any

from fastapi import FastAPI
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route

from flyball.interfaces.client import Rig, RigError

from .server import build
from .tools import TIERS

__all__ = ["Sign", "mount"]

Sign = Callable[[Any, str], str]
"""`sign(principal, tier)`: an `X-Flyball-Principal` for one call a tool makes, fresh each
time. `principal` is the MCP request's own (`request.state.principal`), or None for the
runner's own reads (listing tools, refreshing the schema)."""


# An open runner's names (`interfaces/server/auth.py`'s `LOOPBACK`), any port.
_LOOPBACK = ("localhost", "127.0.0.1", "[::1]")


def _security(app: FastAPI) -> TransportSecuritySettings:
    """The transport's DNS-rebinding check: on, with the open runner's own rule.

    On an open runner (`app.state.auth` is None: no password, no token) only a loopback
    `Host`, and an `Origin`, if any, on a loopback name. A runner with a door is reached by
    whatever name it has on the network, which it does not know; there the door checks
    `Origin` against the request's own `Host`, and a rebound page has no credential to
    send -- the cookie belongs to the real name and the token is never ambient -- so the
    transport's check is left off. So it is on an open runner served on the network by
    `--insecure-open` (`app.state.open_network`): the door holds it to known names (an IP
    address, localhost, this machine's own) and checks `Origin`, and anonymous callers on a
    runner with a door are held to the same names (D-043).
    """
    if getattr(app.state, "auth", None) is not None or getattr(app.state, "open_network", False):
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts = [f"{host}{port}" for host in _LOOPBACK for port in ("", ":*")]
    origins = [f"{scheme}://{host}" for scheme in ("http", "https") for host in hosts]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
    )


def _server(rig: Rig, tier: str, name: str | None, sign: Sign | None) -> Any:
    if sign is None:
        return build(rig, tier, name, host_code=False)
    own = rig.acting(partial(sign, None, tier))

    def caller(ctx: Any) -> Rig:
        principal = getattr(getattr(ctx.request, "state", None), "principal", None)
        if principal is None:  # not through the door: act for no one
            raise RigError(401, "This MCP request carries no principal to act for")
        return own.acting(partial(sign, principal, tier))

    return build(own, tier, name, caller=caller, host_code=False)


def mount(app: FastAPI, rig: Rig, name: str | None = None, *, sign: Sign | None = None) -> None:
    """Add `/mcp/read`, `/mcp/author` and `/mcp/operate` to `app`, and their lifecycle to its.

    `name` is the rig's own name, passed straight through to `build` -- the runner
    already knows it and is not listening yet, so `build` cannot ask itself over `rig`.
    `sign` makes every call a tool makes carry a principal for its caller (`Sign`); None
    sends whatever `rig` sends (a test's in-process client).
    """
    managers = {
        tier: StreamableHTTPSessionManager(
            _server(rig, tier, name, sign),
            json_response=True,
            security_settings=_security(app),
        )
        for tier in TIERS
    }
    # Inserted at the front, not appended: `create_app()` mounts the built dashboard's
    # static files at "/" last (server/app.py), and Starlette matches routes in list
    # order -- appending here would put `/mcp/<tier>` behind that catch-all `Mount`,
    # which only serves GET/HEAD, so every MCP request would 405 on a runner with a
    # built UI (confirmed 18 Sep: production-real, not just a test-ordering quirk).
    for tier, manager in reversed(managers.items()):
        app.router.routes.insert(0, Route(f"/mcp/{tier}", endpoint=StreamableHTTPASGIApp(manager)))

    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[Any]:
        async with contextlib.AsyncExitStack() as stack:
            for manager in managers.values():
                await stack.enter_async_context(manager.run())
            async with inner(app) as state:
                yield state

    app.router.lifespan_context = lifespan
