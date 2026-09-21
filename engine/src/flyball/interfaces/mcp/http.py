"""The same servers over streamable HTTP, mounted in the runner at `/mcp/<mode>`.

A client then needs a URL and nothing installed:

    claude mcp add --transport http rig http://pi:8000/mcp/author

The tools still go through `flyball.client`, so the runner hands `mount` a
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

from flyball.client import Rig

from .server import build
from .tools import MODES

__all__ = ["mount"]


def mount(app: FastAPI, rig: Rig) -> None:
    """Add `/mcp/read`, `/mcp/author` and `/mcp/operate` to `app`, and their lifecycle to its."""
    managers = {
        mode: StreamableHTTPSessionManager(
            build(rig, mode),
            json_response=True,
            # The runner is reached by whatever name the rig has on the network.
            security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
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
