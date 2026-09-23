"""What a reader may see of the runner's own configuration, and what its logs may keep.

Routes that hand out the rig document need only `read` -- an `anonymous: read` visitor and the MCP
`rig_config` tool reach them -- so nothing credential-bearing in `runner:` may ride along: not
`runner.auth`'s token, nor `runner.front`'s password hash, proxy secret or TLS key path. And uvicorn
logs every request's path *with its query*, where `?token=` may be, so its log lines drop the query.
"""

from __future__ import annotations

import logging
from typing import Any

# What a reader may see of `runner:`, key by key: `True` keeps the value, a mapping keeps what
# it names of the section under it. An allowlist, not a list of secrets to strip: a key added to
# `RunnerConfig` later stays hidden until someone decides it is safe to show. Left out: every
# credential (`auth.token`, `front.password`), every path (`store`, `store_dir`, `programs`,
# `tunings`, `drivers`, `front.tls`), the proxy's settings whole (`secret_file`, the JWT's
# issuer and keys, `grants`' subjects), `front.trusted_proxies` and the removed keys.
Allowed = dict[str, "bool | Allowed"]
PUBLIC_RUNNER_KEYS: Allowed = {
    "host": True,
    "port": True,
    "log_level": True,
    "compose": True,
    "mcp": True,
    "root_path": True,
    "allow_save": True,
    "allow_shutdown": True,
    "keep": True,
    "keep_size": True,
    "retain": True,
    "rotate": True,
    "max_store": True,
    "auth": {"anonymous": True},
    "front": {"listen": True, "auth": True, "url": True, "anonymous": True},
}


def without_credentials(document: dict[str, Any]) -> dict[str, Any]:
    """The rig document with only what a reader may see of its `runner:` section."""
    runner = document.get("runner")
    if runner is None:
        return document
    return {**document, "runner": _allowed(runner, PUBLIC_RUNNER_KEYS)}


def _allowed(section: Any, allowed: Allowed) -> dict[str, Any]:
    if not isinstance(section, dict):
        return {}
    out: dict[str, Any] = {}
    for key, rule in allowed.items():
        if key not in section:
            continue
        if rule is True:
            out[key] = section[key]
        elif isinstance(rule, dict) and isinstance(section[key], dict):
            out[key] = _allowed(section[key], rule)
    return out


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
