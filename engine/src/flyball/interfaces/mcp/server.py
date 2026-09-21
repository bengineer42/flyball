"""The stdio server: `flyball-mcp --url http://pi:8000 --mode read|author|operate`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from typing import Any

import anyio
from anyio import to_thread
from mcp import types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server

from flyball.client import Rig, RigError, SchemaError

from .tools import GUIDES, MODES, Tier, Tool, tools_for

DEFAULT_URL = "http://127.0.0.1:8000"

INSTRUCTIONS = {
    "read": "Read-only: nothing here changes the rig or its store.",
    "author": "Reads the rig; saves programs, dashboards and tunings to its store. "
    "Nothing here moves hardware: `check_program` and `check_rig` validate without running.",
    "operate": "Drives the rig. Device commands are `<device>-<command>`; one marked as "
    "interrupting puts a controller in manual. Read `status` and `describe_device` before "
    "commanding something unfamiliar.",
}


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, indent=2, default=str)


def _wire(tool: Tool) -> types.Tool:
    return types.Tool(
        name=tool.name,
        description=tool.description,
        input_schema=tool.schema,
        annotations=types.ToolAnnotations(
            read_only_hint=tool.tier == Tier.READ,
            destructive_hint=tool.destructive,
            open_world_hint=False,
        ),
    )


class Registry:
    """The tools for one rig and mode, built from the schema on first use.

    Lazy so a server mounted in the runner can be made before the runner
    listens, and so the stdio server does not need the rig up to start.
    """

    def __init__(self, rig: Rig, mode: str) -> None:
        self.rig = rig
        self.mode = mode
        self._tools: dict[str, Tool] | None = None
        self._lock = anyio.Lock()

    def reset(self) -> None:
        """Forget the tools: the rig's devices changed, so the next call rebuilds them."""
        self._tools = None

    async def get(self) -> dict[str, Tool]:
        if self._tools is None:
            async with self._lock:
                if self._tools is None:
                    built = await to_thread.run_sync(tools_for, self.rig, self.mode)
                    self._tools = {tool.name: tool for tool in built}
        return self._tools


def build(rig: Rig, mode: str) -> Server[Any]:
    """The MCP server for `rig` in `mode`."""
    registry = Registry(rig, mode)

    async def list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[_wire(t) for t in (await registry.get()).values()])

    async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        tool = (await registry.get()).get(params.name)
        if tool is None:
            return _error(f"no tool {params.name!r}")
        try:
            result = await to_thread.run_sync(tool.run, rig, params.arguments or {})
        except (RigError, SchemaError) as e:
            return _error(str(e))
        if tool.changes_tools:
            registry.reset()
            await to_thread.run_sync(rig.refresh)  # never block the loop: it serves us
            await ctx.session.send_tool_list_changed()
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=_text(result))],
            structured_content=result if isinstance(result, dict) else None,
        )

    async def list_resources(ctx: Any, params: Any) -> types.ListResourcesResult:  # ruff: ignore[unused-async]
        return types.ListResourcesResult(
            resources=[
                types.Resource(
                    name=path.stem,
                    uri=f"flyball://guide/{path.stem}",
                    description=path.read_text().splitlines()[0].lstrip("# "),
                    mime_type="text/markdown",
                )
                for path in sorted(GUIDES.glob("*.md"))
            ]
        )

    async def read_resource(ctx: Any, params: Any) -> types.ReadResourceResult:  # ruff: ignore[unused-async]
        uri = str(params.uri)
        path = GUIDES / f"{uri.removeprefix('flyball://guide/')}.md"
        if not uri.startswith("flyball://guide/") or not path.is_file():
            raise ValueError(f"no resource {uri}")
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=uri, mime_type="text/markdown", text=path.read_text()
                )
            ]
        )

    return _Server(
        "flyball",
        instructions=f"The rig at {rig.url}, mode `{mode}`. {INSTRUCTIONS[mode]}",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_resources=list_resources,
        on_read_resource=read_resource,
    )


class _Server(Server[Any]):
    """Says it will announce tool-list changes, whichever transport drives it."""

    def create_initialization_options(
        self, notification_options: Any = None, *args: Any, **kwargs: Any
    ) -> Any:
        return super().create_initialization_options(
            NotificationOptions(tools_changed=True), *args, **kwargs
        )


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)], is_error=True
    )


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="flyball-mcp", description="An MCP server for a running rig.")
    p.add_argument(
        "--url",
        default=os.environ.get("FLYBALL_URL", DEFAULT_URL),
        help=f"runner base URL (env FLYBALL_URL, default {DEFAULT_URL})",
    )
    p.add_argument("--timeout", type=float, default=30.0, help="seconds per request")
    p.add_argument(
        "--token",
        default=None,
        help="bearer token the runner was started with (env FLYBALL_TOKEN)",
    )
    p.add_argument(
        "--mode",
        choices=list(MODES),
        default="read",
        help="read: questions only; author: also save programs, dashboards, tunings; "
        "operate: also drive the rig (default: read)",
    )
    return p


async def _serve(server: Server[Any]) -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    rig = Rig(args.url, timeout=args.timeout, token=args.token)
    try:
        rig.schema  # fail now, with a message, rather than on the first tool call  # ruff: ignore[useless-expression]
    except RigError as e:
        print(f"flyball-mcp: {e}", file=sys.stderr)
        return 2
    anyio.run(_serve, build(rig, args.mode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
