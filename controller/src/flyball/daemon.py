"""Serve a rig described by a file.

    flyball-daemon rig.toml
    flyball-daemon rig.toml --host 0.0.0.0 --port 8000 --record

Builds the rig from the file (any of `.toml`, `.yaml`, `.json`), starts its
readers, optionally opens a recording session, and serves the HTTP and
websocket API until stopped. An application with hardware the rig file
cannot describe writes its own daemon around [serve][flyball.daemon.serve].
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from flyball.runtime.config import RigConfig, load_rig_config
from flyball.runtime.rig import Rig

log = logging.getLogger("flyball.daemon")


def serve(rig: Rig, host: str = "127.0.0.1", port: int = 8000, log_level: str = "info") -> None:
    """Serve `rig` until interrupted. The rig's readers must already be running."""
    import uvicorn

    from flyball.programmer import Programmer
    from flyball.server import create_app, set_programmer, set_rig

    programmer = Programmer(rig)
    set_rig(rig)
    set_programmer(programmer)
    try:
        uvicorn.run(create_app(), host=host, port=port, log_level=log_level)
    finally:
        programmer.interrupt()
        set_programmer(None)
        set_rig(None)
        rig.stop_recording()
        rig.readers.stop_all()


def start(
    config: RigConfig, record: bool | None = None, store_path: str | Path = "flyball.sqlite"
) -> Rig:
    """Build the rig and, if asked or if the file says so, open a session on it."""
    rig = config.build()
    if record if record is not None else config.recording:
        from flyball.db.sqlite import SqliteStore

        rig.start_recording(SqliteStore(store_path), config=config.model_dump(mode="json"))
        log.info("recording to %s", store_path)
    return rig


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flyball-daemon", description="Serve a rig described by a file."
    )
    p.add_argument("rig", type=Path, help="rig file: .toml, .yaml or .json")
    p.add_argument("--host", default="127.0.0.1", help="bind address (default: loopback only)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--log-level", default="info")
    p.add_argument("--record", action="store_true", help="open a recording session on start")
    p.add_argument(
        "--store", type=Path, default=Path("flyball.sqlite"), help="where sessions are kept"
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=args.log_level.upper())
    try:
        config = load_rig_config(args.rig)
    except Exception as e:  # a bad file is the user's problem, not a traceback
        print(f"flyball-daemon: {args.rig}: {e}", file=sys.stderr)
        return 2
    rig = start(config, record=True if args.record else None, store_path=args.store)
    log.info("serving %s on %s:%d", config.name or args.rig.name, args.host, args.port)
    serve(rig, args.host, args.port, args.log_level)
    return 0


if __name__ == "__main__":
    sys.exit(main())
