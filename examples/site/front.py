"""A development front for several runners on one origin: what nginx does in production.

    uv run python front.py 8080 /humidity=8001 /furnace=8002

Serves the built dashboard (ui/apps/dashboard/dist) under each prefix and
passes that prefix's `/api`, `/ws`, `/mcp`, `/docs` and `/openapi.json` to
the runner on that port, unchanged -- each runner runs with the same
`root_path`. Loopback only. HTTP goes through httpx, websockets through
`websockets`; both are already in flyball's environment.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import uvicorn
import websockets
from starlette.staticfiles import StaticFiles

DIST = Path(__file__).resolve().parents[2] / "ui" / "apps" / "dashboard" / "dist"
PASSED = ("/api", "/ws", "/mcp", "/docs", "/openapi.json")
HOP = {b"host", b"connection", b"upgrade", b"content-length", b"transfer-encoding"}


def main(argv: list[str]) -> None:
    port = int(argv[0])
    sites = {p: int(n) for p, n in (a.split("=", 1) for a in argv[1:])}
    static = StaticFiles(directory=DIST, html=True)
    client = httpx.AsyncClient(timeout=None)

    async def http(scope, receive, send, upstream: int) -> None:
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        headers = [(k, v) for k, v in scope["headers"] if k not in HOP]
        url = f"http://127.0.0.1:{upstream}{scope['path']}"
        if scope["query_string"]:
            url += "?" + scope["query_string"].decode()
        request = client.build_request(scope["method"], url, headers=headers, content=body)
        try:
            r = await client.send(request, stream=True)
        except httpx.ConnectError:  # the runner is down, or restarting
            await send({"type": "http.response.start", "status": 502, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"detail": "the runner is not answering"}'})
            return
        try:
            await send({
                "type": "http.response.start",
                "status": r.status_code,
                "headers": [(k.encode(), v.encode()) for k, v in r.headers.multi_items() if k.lower() not in ("content-length", "transfer-encoding", "connection")],
            })
            async for chunk in r.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        finally:
            await r.aclose()

    async def ws(scope, receive, send, upstream: int) -> None:
        url = f"ws://127.0.0.1:{upstream}{scope['path']}"
        if scope["query_string"]:
            url += "?" + scope["query_string"].decode()
        await receive()  # websocket.connect
        try:
            remote = await websockets.connect(url)
        except websockets.InvalidStatus as e:
            await send({"type": "websocket.close", "code": 4000 + e.response.status_code % 1000})
            return
        except OSError:  # the runner is down, or restarting: the app retries with backoff
            await send({"type": "websocket.close", "code": 1013})
            return
        await send({"type": "websocket.accept"})

        async def downstream() -> None:
            try:
                async for frame in remote:
                    key = "text" if isinstance(frame, str) else "bytes"
                    await send({"type": "websocket.send", key: frame})
            finally:
                await send({"type": "websocket.close"})

        task = asyncio.create_task(downstream())
        try:
            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    break
                await remote.send(message.get("text") or message.get("bytes"))
        finally:
            task.cancel()
            await remote.close()

    async def app(scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            return
        path = scope["path"]
        for prefix, upstream in sites.items():
            if path == prefix:  # `/humidity` -> `/humidity/`, so the page's relative URLs resolve
                await send({"type": "http.response.start", "status": 307, "headers": [(b"location", (prefix + "/").encode())]})
                await send({"type": "http.response.body", "body": b""})
                return
            if not path.startswith(prefix + "/"):
                continue
            rest = path[len(prefix):]
            if any(rest.startswith(p) for p in PASSED):
                await (ws if scope["type"] == "websocket" else http)(scope, receive, send, upstream)
            elif scope["type"] == "http":
                await static({**scope, "path": rest, "root_path": prefix}, receive, send)
            return
        if scope["type"] == "http":
            body = "\n".join(f"{p}/" for p in sites).encode()
            await send({"type": "http.response.start", "status": 404, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"sites:\n" + body})

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main(sys.argv[1:])
