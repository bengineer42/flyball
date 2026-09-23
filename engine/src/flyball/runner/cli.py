"""The `flyball-runner` command line: its flags, and settling them against a rig file."""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from collections.abc import Sequence
from pathlib import Path

from flyball.runtime.config import AuthConfig, RunnerConfig


def parser() -> argparse.ArgumentParser:
    """A flag mirroring a `runner:` key defaults to None -- unset -- so the file's value stands."""
    p = argparse.ArgumentParser(
        prog="flyball-runner", description="Serve a rig described by a file."
    )
    p.add_argument(
        "rig",
        type=Path,
        nargs="*",
        help="rig file(s): .toml, .yaml or .json; later overlay earlier. None: an empty rig,"
        " built up through the API (then --store says where sessions and versions go)",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="start from the store's last rig version instead of the files: what was added"
        " through the API and not saved comes back",
    )
    p.add_argument(
        "--host",
        help="bind address (default: loopback only); beyond loopback an open runner (no token)"
        " is served on 127.0.0.1 instead, unless --insecure-open. Ignored with --front-dir",
    )
    p.add_argument(
        "--front-dir",
        type=Path,
        metavar="DIR",
        help="started by a front (`flyball run`, flyballd): bind the endpoint DIR says and take"
        " only the principal it signs; runner.auth, --token, --anonymous, --host and --port are"
        " ignored. Unsafe or incomplete: exit 4. No environment variable",
    )
    p.add_argument(
        "--compose",
        action="store_const",
        const=True,
        help="allow the rig to be built up over the API on a hardware rig"
        " (a simulated or bare rig always may)",
    )
    p.add_argument(
        "--password",
        default=os.environ.get("FLYBALL_PASSWORD") or None,
        help="removed: ignored with a warning (env FLYBALL_PASSWORD too). A bare runner takes a"
        " token; for a password login run it under `flyball run`",
    )
    token = p.add_mutually_exclusive_group()
    token.add_argument(
        "--token",
        default=os.environ.get("FLYBALL_TOKEN") or None,
        help="bearer token for the CLI, MCP clients, scripts and the UI's login (env"
        " FLYBALL_TOKEN); default: none. Without one the runner is open, and served on loopback"
        " only",
    )
    token.add_argument(
        "--token-file",
        type=Path,
        metavar="PATH",
        help="read the token from this file (beats FLYBALL_TOKEN); unreadable: nobody gets in",
    )
    p.add_argument(
        "--insecure-open",
        action="store_const",
        const=True,
        default=True if _truthy(os.environ.get("FLYBALL_INSECURE_OPEN")) else None,
        help="serve with no token on the address asked for, even beyond"
        " loopback: anyone who reaches it may operate the rig (env FLYBALL_INSECURE_OPEN=1)."
        " Per run only; there is no rig-file key. Without it such a runner serves on 127.0.0.1",
    )
    p.add_argument(
        "--anonymous",
        choices=("none", "read"),
        default=os.environ.get("FLYBALL_ANONYMOUS") or None,
        help="what a caller with no session and no token may do: nothing, or read every GET and"
        " stream (env FLYBALL_ANONYMOUS); default: none",
    )
    p.add_argument(
        "--session",
        default=os.environ.get("FLYBALL_SESSION") or None,
        metavar="DURATION",
        help="removed: ignored with a warning (env FLYBALL_SESSION too); a session lasts 12h",
    )
    p.add_argument(
        "--no-mcp",
        dest="mcp",
        action="store_const",
        const=False,
        default=False if os.environ.get("FLYBALL_NO_MCP") else None,
        help="do not mount the MCP servers at /mcp (env FLYBALL_NO_MCP=1); default: mounted",
    )
    p.add_argument(
        "--root-path",
        default=os.environ.get("FLYBALL_ROOT_PATH") or None,
        metavar="/PREFIX",
        help="serve everything under this path, e.g. /flyball/humidity (env FLYBALL_ROOT_PATH);"
        " default: the root",
    )
    p.add_argument(
        "--allow-save",
        action="store_const",
        const=True,
        help="let the API write rig files: /api/rig/save to a path, /api/sim/save; default: no",
    )
    p.add_argument(
        "--allow-shutdown",
        action="store_const",
        const=True,
        help="let the API stop or restart the runner (/api/runner/shutdown, /restart); default: no",
    )
    p.add_argument("--port", type=int, help="TCP port (default 8000)")
    p.add_argument(
        "--keep",
        default=os.environ.get("FLYBALL_KEEP") or None,
        metavar="DURATION",
        help="how much the scratch record holds while nothing is recorded, in the rig's clock"
        " (env FLYBALL_KEEP; default 1h; 0 keeps none)",
    )
    p.add_argument(
        "--keep-size",
        default=os.environ.get("FLYBALL_KEEP_SIZE") or None,
        metavar="SIZE",
        help="the most the scratch record may take on disk (env FLYBALL_KEEP_SIZE; default 256MB)",
    )
    p.add_argument(
        "--retain",
        default=os.environ.get("FLYBALL_RETAIN") or None,
        metavar="DURATION",
        help="delete an unpinned session this long after it ended, e.g. 30d"
        " (env FLYBALL_RETAIN; default 0: keep every session)",
    )
    p.add_argument(
        "--rotate",
        default=os.environ.get("FLYBALL_ROTATE") or None,
        metavar="DURATION",
        help="close a recording at this length and continue it in a new session, e.g. 24h"
        " (env FLYBALL_ROTATE; default 0: never)",
    )
    p.add_argument(
        "--max-store",
        default=os.environ.get("FLYBALL_MAX_STORE") or None,
        metavar="SIZE",
        help="keep the store under this size by deleting the oldest data of any kind, never"
        " pinned, e.g. 20GB (env FLYBALL_MAX_STORE; default 0: no cap)",
    )
    p.add_argument("--log-level", help="uvicorn's (default info)")
    p.add_argument("--record", action="store_true", help="open a recording session on start")
    p.add_argument(
        "--store",
        type=Path,
        help="where sessions are kept (default: '<rig>.sqlite' beside the first rig file)",
    )
    p.add_argument(
        "--programs",
        type=Path,
        help="directory of program files to import (default: 'programs' beside the first rig file)",
    )
    p.add_argument(
        "--tunings",
        type=Path,
        help="directory of control-law configs to load (default: 'tunings' beside the first rig"
        " file)",
    )
    p.add_argument(
        "--drivers",
        type=Path,
        help="directory of driver .py files to import at start and on /api/drivers/reload"
        " (default: 'drivers' beside the first rig file)",
    )
    p.add_argument(
        "--set",
        dest="sets",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="override a value after loading, e.g. devices.furnace.noise=0.3; repeatable",
    )
    return p


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def settle(
    section: RunnerConfig | None,
    args: argparse.Namespace,
    first: Path,
    name: str | None = None,
    layers: Sequence[Path] = (),
) -> RunnerConfig:
    """The file's `runner:` section under the command line, with every directory resolved.

    A flag given (or its environment variable) beats the file; a path in the
    file is taken relative to the first rig file's directory. A directory not
    named at all is the conventional one (`programs/`, `tunings/`, `drivers/`)
    beside the first file that actually has it -- checked in `layers` order (the
    command line, then each file an `extends` chain pulled in), not beside the
    first rig file alone, so a deployment wrapper that `extends` a shared base
    still finds the base's libraries. Beside the first file if none of them do,
    same as before. The store is `--store`, else `store` in the file, else
    `<store_dir>/<name>.sqlite` (the rig's name, else the file's stem), else
    `<rig>.sqlite` beside the file.
    """
    given = {
        key: value
        for key in RunnerConfig.model_fields
        if key != "auth" and (value := getattr(args, key, None)) is not None
    }
    if (
        getattr(args, "front_dir", None) is not None
        and "root_path" not in given
        and section is not None
        and section.root_path
    ):
        # A fronted runner is reached wherever its front put it -- "" under
        # `flyball run`, a manifest's root_path under flyballd, always passed as
        # --root-path when it matters (given["root_path"] above) -- never the rig
        # file's own runner.root_path, which would put the handshake out of step
        # with the front (a 404 on GET <front's root>/api/auth/front). Ignored
        # the same way a fronted runner already ignores runner.auth.
        print(
            f"flyball-runner: WARNING: runner.root_path {section.root_path!r} is ignored"
            " when fronted (--front-dir): the front decides where the rig is served"
            " (flyballd: the manifest's root_path; `flyball run`: /)",
            file=sys.stderr,
            flush=True,
        )
        given["root_path"] = None
    settings = (section or RunnerConfig()).model_copy(update=given)
    auth = {
        key: value
        for key in AuthConfig.model_fields
        if (value := getattr(args, key, None)) is not None
    }
    token_file: Path | None = getattr(args, "token_file", None)
    if token_file is not None:
        auth["token"] = _read_token(token_file)
    if auth:
        settings.auth = settings.auth.model_copy(update=auth)
    for key in ("store", "store_dir", "programs", "tunings", "drivers"):
        path: Path | None = getattr(settings, key)
        if path is not None and key not in given and not path.is_absolute():
            setattr(settings, key, first.parent / path)  # the file's: relative to the rig
    if settings.store is None:
        if settings.store_dir is not None:
            settings.store = settings.store_dir / f"{name or first.stem}.sqlite"
        else:
            settings.store = first.with_suffix(".sqlite")
    for key in ("programs", "tunings", "drivers"):
        if getattr(settings, key) is None:
            setattr(settings, key, _find_beside(key, first, layers))
    return settings


def _read_token(path: Path) -> str:
    """The token in `path`; unreadable or empty, one no one knows (D-028: no one gets in)."""
    try:
        token = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as e:
        token, why = "", str(e)
    else:
        why = f"{path} is empty"
    if token:
        return token
    print(
        f"flyball-runner: WARNING: --token-file: {why}; serving with a token no one knows,"
        " so nothing but a restart with a readable token gets in",
        file=sys.stderr,
        flush=True,
    )
    return secrets.token_urlsafe(32)


def _find_beside(key: str, first: Path, layers: Sequence[Path]) -> Path:
    """`<dir>/<key>` beside the first file that has one, else beside `first`."""
    for layer in (first, *layers):
        candidate = layer.parent / key
        if candidate.is_dir():
            return candidate
    return first.parent / key
