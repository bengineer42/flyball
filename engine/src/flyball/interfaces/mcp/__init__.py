"""An MCP server for a running rig, in three tiers.

`read` answers questions: nothing changes anywhere. `author` adds the store:
programs, dashboards and tunings can be saved, never hardware touched.
`operate` adds the rig itself: device commands, demands, controllers,
programs, recording. Every tool is one HTTP call through `flyball.interfaces.client`,
so the server runs wherever the CLI does.

    flyball-mcp --url http://pi:8000 --tier author
"""

from .tools import TIERS, Tier, Tool, tools_for

__all__ = ["TIERS", "Tier", "Tool", "tools_for"]
