# Models over MCP

**What.** The daemon serves the Model Context Protocol on its own port at
`/mcp/read`, `/mcp/author` and `/mcp/operate` -- three tiers, narrowest
first -- so Claude Desktop, Claude Code or any MCP client can read the rig,
write rig files, drivers and programs, or drive it.

**What comes through.** Tools generated from the same routes the UI uses:
readings and health in `read`; attaching devices, checking files and
scaffolding a driver in `author`; demands, controllers and programs in
`operate`. A client in read mode is never told a moving tool exists.

**Configure.** On by default; `--no-mcp` (or `mcp: false` in the
[`daemon:` section](../2-config/daemon.md)) turns it off. A daemon with a
token requires it on `/mcp` too; one with only a password needs a token
added before a model outside it can connect. The connect lines and a client config
block are on the UI's [Rig page](../1-running/ui/rig.md).

**Everything else** -- the tiers, the token, what the model sees -- is
[The MCP server](../4-server/mcp.md).
