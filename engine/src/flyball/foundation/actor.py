"""Who did something: the `actor` on every record of an action (D-081).

A stop, a latch, a write, a command run while the rig is stopped, an audited request:
each says who acted as one `Actor` -- `{principal, kind, via}`, plus the login (`sid`)
and a `message` where there is more to say. `user` is only auth's account record.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

__all__ = ["Actor", "Via"]

type Via = Literal["http", "mcp", "signal", "rig"]
"""How it came in: `http` (the UI, the API), `mcp` (a tool's own call), `signal` (the
break-glass `SIGUSR1`, the runner's shutdown), `rig` (from inside: a program, a controller,
a stop)."""


@dataclass(frozen=True, slots=True)
class Actor:
    """Who acted: the principal, its kind, and the way it came in."""

    principal: str
    """Who: a principal's id (`token:ops`, `local:console`, `anon:`), or the rig's own
    (`program`, a controller's name, `stop`)."""
    kind: str
    """What it is: a principal's kind (`human`, `service`, `agent`), or `program`,
    `controller`, `rule` for the rig's own."""
    via: Via
    sid: str = ""
    """The login it came through; `""` for none."""
    message: str = ""
    """Anything more about the caller, e.g. `from 127.0.0.1`, `signal from pid 4121 uid 1000`."""

    @property
    def person(self) -> bool:
        """A person at a UI or the HTTP API: a human principal, not an agent through MCP."""
        return self.kind == "human" and self.via != "mcp"

    def as_dict(self) -> dict[str, Any]:
        """As the wire and the store carry it: `{principal, kind, via, sid, message}`."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Actor:
        """Back from `as_dict`'s shape (a stored row)."""
        return cls(
            principal=str(data.get("principal", "")),
            kind=str(data.get("kind", "")),
            via=data.get("via", "rig"),
            sid=str(data.get("sid", "")),
            message=str(data.get("message", "")),
        )
