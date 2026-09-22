"""The faces of the rig boundary: server (HTTP/websocket), client, and mcp.

All three talk to a running rig from outside it -- distinct from the internal
layers (`foundation`, `control`, `runtime`, ...). `server` exposes a rig,
`client` consumes it from another Python process, and `mcp` is built entirely
on `client` (every tool is one HTTP call through it).
"""
