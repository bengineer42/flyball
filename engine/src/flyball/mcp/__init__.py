"""An MCP server for a running rig, in three modes.

`read` answers questions: nothing changes anywhere. `author` adds the store:
programs, dashboards and tunings can be saved, never hardware touched.
`operate` adds the rig itself: device commands, demands, controllers,
programs, recording. Every tool is one HTTP call through `flyball.client`,
so the server runs wherever the CLI does.

    flyball-mcp --url http://pi:8000 --mode author
"""

from .tools import MODES, Tier, Tool, tools_for

__all__ = ["MODES", "Tier", "Tool", "tools_for"]
