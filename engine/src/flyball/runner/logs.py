"""The runner's log lines: every one stamped with the time, its zone included.

Under `flyballd` (or systemd) stdout and stderr are the log file, and a line with no
time cannot be matched to a reading, a restart or an event.
"""

from __future__ import annotations

import logging

DATEFMT = "%Y-%m-%dT%H:%M:%S%z"
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure(level: str) -> None:
    """The root logger: what `flyball.*` and every library without its own handler use."""
    logging.basicConfig(level=level.upper(), format=FORMAT, datefmt=DATEFMT)


def stamp_uvicorn() -> None:
    """Put the time in front of uvicorn's own lines (server and access log).

    `uvicorn.Config` installs its handlers with formatters of its own, which carry
    no time; this keeps them (their level prefix, colour choice and access fields)
    and prefixes the stamp. Call after the `Config` is made.
    """
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            old = handler.formatter
            fmt = getattr(old, "_fmt", None)
            if old is None or fmt is None or fmt.startswith("%(asctime)s"):
                continue
            kwargs = {}
            if (colours := getattr(old, "use_colors", None)) is not None:
                kwargs["use_colors"] = colours
            handler.setFormatter(type(old)(fmt=f"%(asctime)s {fmt}", datefmt=DATEFMT, **kwargs))
