"""What a reader may see of the runner's own configuration.

Routes that hand out the rig document need only `read` -- an `anonymous: read` visitor and the MCP
`rig_config` tool reach them -- so `runner.auth`'s credentials must never ride along.
"""

from __future__ import annotations

from typing import Any

# What `runner.auth` may show a reader. An allowlist, not a list of secrets to strip: a credential
# added to `AuthConfig` later stays hidden until someone decides it is safe to show.
PUBLIC_AUTH_KEYS = frozenset({"anonymous", "session"})


def without_credentials(document: dict[str, Any]) -> dict[str, Any]:
    """The rig document minus `runner.auth`'s password, token and signing secret."""
    runner = document.get("runner")
    if not isinstance(runner, dict) or not isinstance(runner.get("auth"), dict):
        return document
    auth = {k: v for k, v in runner["auth"].items() if k in PUBLIC_AUTH_KEYS}
    return {**document, "runner": {**runner, "auth": auth}}
