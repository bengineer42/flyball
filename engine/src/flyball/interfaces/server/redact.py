"""What a reader may see of the runner's own configuration, and what its logs may keep.

Routes that hand out the rig document need only `read` -- an `anonymous: read` visitor and the MCP
`rig_config` tool reach them -- so `runner.auth`'s credentials must never ride along. And uvicorn
logs every request's path *with its query*, where `?token=` may be, so its log lines drop the query.
"""

from __future__ import annotations

import logging
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


class QueryRedaction(logging.Filter):
    """Cut the query off every path in uvicorn's request lines: `/api/x?token=t` -> `/api/x?…`.

    A request (`uvicorn.access`) and a websocket (`uvicorn.error`, `"WebSocket %s"`) line
    each carry the path as an argument; the query goes, whatever it holds -- `?token=`
    is the one that matters, and a decoded `%74oken=` counts as one too, so no key is
    safe to keep by name.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and (
            '"%s %s HTTP/' in str(record.msg) or ('"WebSocket %s"' in str(record.msg))
        ):
            record.args = tuple(_cut(arg) for arg in record.args)
        return True


def _cut(arg: object) -> object:
    if isinstance(arg, str) and arg.startswith("/") and "?" in arg:
        return arg.split("?", 1)[0] + "?…"
    return arg


def redact_access_logs() -> None:
    """Install `QueryRedaction` on uvicorn's loggers, once however often it is called."""
    for name in ("uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, QueryRedaction) for f in logger.filters):
            logger.addFilter(QueryRedaction())
