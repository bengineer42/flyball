"""Custom hatchling build hook: pull the built dashboard UI into the wheel.

`ui/apps/dashboard/dist` lives outside this package (`ui/` is a separate npm
workspace, two directories up from `engine/`), so a plain static
`force-include` in `pyproject.toml` would be enough for CI, which always runs
`npm run build` before `hatch build`/`uv build`. But `force-include` errors
hard (`FileNotFoundError`) when its source is missing, and `dist/` is
gitignored -- absent on every ordinary dev checkout -- so a static entry
would break `uv sync`/`make test`'s editable install for anyone who hasn't
built the UI. Adding the mapping here, conditionally, keeps a wheel built
after `npm run build` fully packaged while leaving a plain dev checkout
(or a headless/no-UI build) alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class DashboardStaticHook(BuildHookInterface):  # type: ignore[type-arg]
    """Force-includes the built dashboard's static files, if they exist."""

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """Add the dashboard dist to force-include only when it's actually built."""
        dist = Path(self.root).parent / "ui" / "apps" / "dashboard" / "dist"
        if dist.is_dir():
            build_data.setdefault("force_include", {})[str(dist)] = "flyball/server/static"
