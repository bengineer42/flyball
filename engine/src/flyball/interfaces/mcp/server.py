"""The stdio server: `flyball-mcp --url http://pi:8000 --mode read|author|operate`."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from collections.abc import Callable, Sequence
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING, Any

import flyball
from flyball.interfaces.client import Rig, RigError, SchemaError

from .tools import GUIDES, MODES, Tier, Tool, tools_for

if TYPE_CHECKING:
    # Only for annotations (`from __future__ import annotations` keeps these
    # as strings at runtime): the real imports are inside `build`/`main`, so
    # the `mcp` package (the `mcp` extra) is not needed to build the parser
    # or print --help on a bare install -- see `_require` below.
    from mcp import types
    from mcp.server.lowlevel import Server

DEFAULT_URL = "http://127.0.0.1:8000"


def _require(extra: str, modules: Sequence[str]) -> None:
    """Import each of `modules`; on the first genuinely missing one, one line and exit(2).

    A private copy of [flyball.foundation.optional.require][] -- this package
    is one of the ones import-linter's "stand alone" contract keeps free of
    `flyball.foundation` and everything below it, so it runs (this check
    included) against nothing but the rig it is pointed at.
    """
    for module in modules:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as e:
            if e.name != module and not (e.name and module.startswith(e.name + ".")):
                raise  # a broken import inside an installed package, not a missing one
            print(
                f"flyball-mcp: needs the {extra} extra -- pip install 'flyball[{extra}]'"
                f" (missing: {module})",
                file=sys.stderr,
            )
            raise SystemExit(2) from None


def _version() -> str:
    try:
        return _pkg_version("flyball")
    except PackageNotFoundError:
        return flyball.__version__


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


class ToolCache:
    """The tools for one rig and mode, built from the schema on first use.

    Lazy so a server mounted in the runner can be made before the runner
    listens, and so the stdio server does not need the rig up to start.
    `anyio` is only imported inside these methods (not at class-body time),
    so building a `ToolCache` needs the `mcp` extra, but merely importing
    this module does not -- `flyball-mcp --help` still works on a bare
    install (see `require` in `main`).
    """

    def __init__(self, rig: Rig, mode: str, host_code: bool = True) -> None:
        import anyio

        self.rig = rig
        self.mode = mode
        self.host_code = host_code
        self._tools: dict[str, Tool] | None = None
        self._lock = anyio.Lock()

    def reset(self) -> None:
        """Forget the tools: the rig's devices changed, so the next call rebuilds them."""
        self._tools = None

    async def get(self) -> dict[str, Tool]:
        if self._tools is None:
            async with self._lock:
                if self._tools is None:
                    from anyio import to_thread

                    built = await to_thread.run_sync(
                        lambda: tools_for(self.rig, self.mode, host_code=self.host_code)
                    )
                    self._tools = {tool.name: tool for tool in built}
        return self._tools


def build(
    rig: Rig,
    mode: str,
    name: str | None = None,
    *,
    caller: Callable[[Any], Rig] | None = None,
    host_code: bool = True,
) -> Server[Any]:
    """The MCP server for `rig` in `mode`.

    `name` is the rig's own name, for `instructions`; give it when the caller already
    knows it (the runner mounting this in-process) rather than have `build` fetch it --
    the runner is not listening yet when it mounts this, and the rig's own URL, which
    only makes sense from the runner's loopback, is worse than no address at all.

    `caller`, given a tool call's request context, is the rig to run that tool on: the
    runner's HTTP mount makes it act as whoever called (see `http.py`); a
    [RigError][flyball.interfaces.client.RigError] from it refuses the call. None: every
    tool runs on `rig`. `rig` itself lists the tools and refreshes the schema.
    `host_code=False` leaves out the tools that run code on this machine.

    Needs the `mcp` extra: every import it uses is deferred to inside this
    function (and the functions it builds), so loading this module -- for
    `parser()`/`--help`, or before `main` has checked for the extra -- does
    not.
    """
    from anyio import to_thread
    from mcp import types
    from mcp.server.lowlevel import NotificationOptions, Server
    from mcp.shared.exceptions import MCPError
    from mcp_types import INVALID_PARAMS

    class _Server(Server[Any]):
        """Says it will announce tool-list changes, whichever transport drives it."""

        def create_initialization_options(
            self, notification_options: Any = None, *args: Any, **kwargs: Any
        ) -> Any:
            return super().create_initialization_options(
                NotificationOptions(tools_changed=True), *args, **kwargs
            )

    def _wire(tool: Tool) -> types.Tool:
        return types.Tool(
            name=tool.name,
            description=tool.description,
            input_schema=tool.schema,
            output_schema=tool.output_schema,
            annotations=types.ToolAnnotations(
                read_only_hint=tool.tier == Tier.READ,
                destructive_hint=tool.destructive,
                open_world_hint=False,
            ),
        )

    def _error(message: str) -> types.CallToolResult:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=message)], is_error=True
        )

    registry = ToolCache(rig, mode, host_code)

    async def list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[_wire(t) for t in (await registry.get()).values()])

    async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        tool = (await registry.get()).get(params.name)
        if tool is None:
            return _error(f"no tool {params.name!r}")
        arguments = params.arguments or {}
        missing = [k for k in tool.schema.get("required", ()) if k not in arguments]
        if missing:
            raise MCPError(INVALID_PARAMS, f"{tool.name}: missing required argument {missing[0]!r}")
        try:
            acting = rig if caller is None else caller(ctx)
            result = await to_thread.run_sync(tool.run, acting, arguments)
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
                    description=path.read_text(encoding="utf-8").splitlines()[0].lstrip("# "),
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
                    uri=uri, mime_type="text/markdown", text=path.read_text(encoding="utf-8")
                )
            ]
        )

    rig_desc = f"The rig `{name}`" if name else "This rig"
    return _Server(
        "flyball",
        version=_version(),
        instructions=f"{rig_desc}, mode `{mode}`. {INSTRUCTIONS[mode]}",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_resources=list_resources,
        on_read_resource=read_resource,
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
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    # Checked before anything that needs the `mcp` extra, so `--help` above
    # still works on a bare `pip install flyball` and any other failure gets
    # one clear line instead of a traceback out of `import mcp`/`import anyio`.
    _require("mcp", ["mcp", "httpx"])
    import anyio

    rig = Rig(args.url, timeout=args.timeout, token=args.token)
    try:
        rig.schema  # fail now, with a message, rather than on the first tool call  # ruff: ignore[useless-expression]
    except RigError as e:
        print(f"flyball-mcp: {e}", file=sys.stderr)
        return 2
    try:
        name = rig.get("/api/health").get("rig")
    except RigError:  # cosmetic only: `instructions` falls back to "This rig"
        name = None
    anyio.run(_serve, build(rig, args.mode, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
