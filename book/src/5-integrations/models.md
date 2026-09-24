# Models over MCP

**What.** The runner serves the Model Context Protocol on its own port at
`/mcp/read`, `/mcp/author` and `/mcp/operate` -- three tiers, narrowest
first -- so Claude Desktop, Claude Code or any MCP client can read the rig,
write rig files, drivers and programs, or drive it.

**What comes through.** Tools generated from the same routes the UI uses:
readings and health in `read`; attaching devices, checking files and
scaffolding a driver in `author`; demands, controllers and programs in
`operate`. A client on the read tier is never told a moving tool exists.

**Configure.** On by default; `--no-mcp` (or `mcp: false` in the
[`runner:` section](../2-config/runner.md)) turns it off. Behind a front
with a sign-in, a model needs a named token (`flyball token create
--kind agent`), sent as a bearer header; its scopes cap what each mode
lets it do. The `local` shape needs none, on the machine itself. The connect lines and a client config
block are on the UI's [rig file tab](../1-running/ui/rig.md).

**Everything else** -- the tiers, the token, what the model sees -- is
[The MCP server](../4-server/mcp.md).
