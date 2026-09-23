"""The same servers over streamable HTTP, mounted in the runner at `/mcp/<mode>`.

A client then needs a URL and nothing installed:

    claude mcp add --transport http rig http://pi:8000/mcp/author

The tools still go through `flyball.interfaces.client`, so the runner hands `mount` a
client pointed back at itself.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route

from flyball.interfaces.client import Rig

from .server import build
from .tools import MODES

__all__ = ["mount"]


# An open runner's names (`interfaces/server/auth.py`'s `LOOPBACK`), any port.
_LOOPBACK = ("localhost", "127.0.0.1", "[::1]")


def _security(app: FastAPI) -> TransportSecuritySettings:
    """The transport's DNS-rebinding check: on, with the open runner's own rule.

    On an open runner (`app.state.auth` is None: no password, no token) only a loopback
    `Host`, and an `Origin`, if any, on a loopback name. A runner with a door is reached by
    whatever name it has on the network, which it does not know; there the door checks
    `Origin` against the request's own `Host`, and a rebound page has no credential to
    send -- the cookie belongs to the real name and the token is never ambient -- so the
    transport's check is left off.
    """
    if getattr(app.state, "auth", None) is not None:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts = [f"{host}{port}" for host in _LOOPBACK for port in ("", ":*")]
    origins = [f"{scheme}://{host}" for scheme in ("http", "https") for host in hosts]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
    )


def mount(app: FastAPI, rig: Rig, name: str | None = None) -> None:
    """Add `/mcp/read`, `/mcp/author` and `/mcp/operate` to `app`, and their lifecycle to its.

    `name` is the rig's own name, passed straight through to `build` -- the runner
    already knows it and is not listening yet, so `build` cannot ask itself over `rig`.
    """
    managers = {
        mode: StreamableHTTPSessionManager(
            build(rig, mode, name),
            json_response=True,
            security_settings=_security(app),
        )
        for mode in MODES
    }
    # Inserted at the front, not appended: `create_app()` mounts the built dashboard's
    # static files at "/" last (server/app.py), and Starlette matches routes in list
    # order -- appending here would put `/mcp/<mode>` behind that catch-all `Mount`,
    # which only serves GET/HEAD, so every MCP request would 405 on a runner with a
    # built UI (confirmed 18 Sep: production-real, not just a test-ordering quirk).
    for mode, manager in reversed(managers.items()):
        app.router.routes.insert(0, Route(f"/mcp/{mode}", endpoint=StreamableHTTPASGIApp(manager)))

    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[Any]:
        async with contextlib.AsyncExitStack() as stack:
            for manager in managers.values():
                await stack.enter_async_context(manager.run())
            async with inner(app) as state:
                yield state

    app.router.lifespan_context = lifespan
