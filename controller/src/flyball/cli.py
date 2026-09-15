"""Command line for a running rig, built from the schema it publishes.

``flyball`` fetches ``/api/schema`` once (cached under ``~/.cache/flyball``)
and builds a subcommand per device and per device command from it, so a new
actuator with ``@command`` methods is on the command line with no code here.
Help text is the docstrings, units and choices the schema carries::

    flyball actuators                       what is attached
    flyball pumps                           config, settings and state
    flyball pumps set_blend --wet-fraction 0.25 --flow.tag absolute --flow.flow 8
    flyball pumps set_flows 2 6             a single argument may be positional
    flyball signals                         what a program is waiting on
    flyball signal fire lid                 answer it
    flyball watch loops                     one JSON line per frame

Argument flags come from the argument schema: ``--name`` per property,
dotted for nested objects, ``--flag/--no-flag`` for booleans, choices for
enums. Any flag also takes a JSON literal, for shapes the flags cannot spell.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from flyball.client import Rig, RigError, SchemaError, Unreachable

DEFAULT_URL = "http://127.0.0.1:8000"
EXIT_ERROR = 1
EXIT_UNREACHABLE = 3
STREAMS = ("samples", "loops", "actuators", "readers", "signals")


# region Schema cache


def _cache_path(url: str) -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "flyball"
    return root / (hashlib.sha1(url.encode()).hexdigest()[:12] + ".json")


def load_schema(url: str, refresh: bool, offline: Path | None) -> tuple[dict[str, Any], str]:
    """The rig's schema and where it came from: the rig, the cache, or a file."""
    if offline is not None:
        return json.loads(offline.read_text()), f"file {offline}"
    cache = _cache_path(url)
    if not refresh and cache.exists():
        try:
            return json.loads(cache.read_text()), "cache"
        except ValueError:
            pass
    schema = Rig(url).refresh()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(schema))
    return schema, "rig"


# endregion
# region Schema -> argparse


def _resolve(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        schema = defs[schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def _describe(schema: dict[str, Any], defs: dict[str, Any]) -> str:
    """One line of help from a property schema: description, unit, bounds, default."""
    schema = _resolve(schema, defs)
    parts = [schema.get("description") or ""]
    if unit := schema.get("unit"):
        parts.append(f"({unit})")
    bounds = []
    if (lo := schema.get("minimum")) is not None:
        bounds.append(f">= {lo}")
    if (lo := schema.get("exclusiveMinimum")) is not None:
        bounds.append(f"> {lo}")
    if (hi := schema.get("maximum")) is not None:
        bounds.append(f"<= {hi}")
    if (hi := schema.get("exclusiveMaximum")) is not None:
        bounds.append(f"< {hi}")
    if bounds:
        parts.append(", ".join(bounds))
    if "default" in schema:
        parts.append(f"[default: {json.dumps(schema['default'])}]")
    return " ".join(p for p in parts if p).replace("%", "%%")  # argparse formats help with %


def _choices(schema: dict[str, Any], defs: dict[str, Any]) -> list[tuple[Any, str]] | None:
    """``(value, label)`` pairs for an enum or a oneOf of constants."""
    schema = _resolve(schema, defs)
    if "enum" in schema:
        return [(v, str(v)) for v in schema["enum"]]
    options = schema.get("oneOf") or schema.get("anyOf")
    if options and all("const" in _resolve(o, defs) for o in options):
        return [(_resolve(o, defs)["const"], _resolve(o, defs).get("title", "")) for o in options]
    return None


def _json_or_str(text: str) -> Any:
    """A flag value: JSON if it parses, else the text. ``"4"`` is 4, ``"on"`` is ``"on"``."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def add_arguments(
    parser: argparse.ArgumentParser,
    schema: dict[str, Any],
    defs: dict[str, Any],
    prefix: str = "",
    required: bool = True,
    variant: str | None = None,
) -> None:
    """Flags for every property of an object schema, dotted for nested objects."""
    schema = _resolve(schema, defs)
    required_names = set(schema.get("required", [])) if required else set()
    for name, prop in schema.get("properties", {}).items():
        dotted = f"{prefix}{name}"
        flag = "--" + dotted.replace("_", "-")
        resolved = _resolve(prop, defs)
        help_text = _describe(prop, defs)
        if variant:
            help_text = f"[{variant}] {help_text}"
        options = resolved.get("oneOf") or resolved.get("anyOf")

        if (choices := _choices(prop, defs)) is not None:
            labels = "; ".join(
                f"{v}: {label}" for v, label in choices if label and label != str(v)
            ).replace("%", "%%")
            parser.add_argument(
                flag,
                dest=dotted,
                type=_json_or_str,
                choices=[v for v, _ in choices],
                required=name in required_names,
                help=f"{help_text} {labels}".strip(),
            )
        elif resolved.get("type") == "boolean":
            parser.add_argument(
                flag,
                dest=dotted,
                action=argparse.BooleanOptionalAction,
                required=name in required_names,
                help=help_text,
            )
        elif "properties" in resolved:
            add_arguments(parser, resolved, defs, f"{dotted}.", name in required_names, variant)
        elif options and any("properties" in _resolve(o, defs) for o in options):
            # A union of objects: every branch's flags, each labelled with its branch.
            for option in options:
                branch = _resolve(option, defs)
                if "properties" not in branch:
                    continue
                tag = branch.get("title") or "variant"
                if "tag" in branch.get("properties", {}):
                    tag = str(_resolve(branch["properties"]["tag"], defs).get("const", tag))
                add_arguments(parser, branch, defs, f"{dotted}.", False, tag)
            parser.add_argument(
                flag, dest=dotted, type=_json_or_str, help=f"{help_text} (as a JSON literal)"
            )
        else:
            parser.add_argument(
                flag,
                dest=dotted,
                type=_json_or_str,
                required=name in required_names,
                help=help_text,
                metavar=(resolved.get("type") or "value").upper(),
            )


def body_from(args: argparse.Namespace, skip: frozenset[str]) -> dict[str, Any]:
    """Nest ``a.b.c`` flags back into an object; drop what was not given."""
    body: dict[str, Any] = {}
    for key, value in vars(args).items():
        if key in skip or value is None:
            continue
        target = body
        *path, last = key.split(".")
        for part in path:
            if not isinstance(target.get(part), dict):
                target[part] = {}
            target = target[part]
        target[last] = value
    return body


# endregion
# region Parser


_META = frozenset({
    "url",
    "timeout",
    "json",
    "refresh",
    "offline",
    "fn",
    "device",
    "kind",
    "command",
    "positional",
    "stream",
    "name",
    "path",
})


def _global_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--url",
        default=os.environ.get("FLYBALL_URL", DEFAULT_URL),
        help=f"daemon base URL (env FLYBALL_URL, default {DEFAULT_URL})",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="seconds per request")
    parser.add_argument("--json", action="store_true", help="print raw JSON, one document per line")
    parser.add_argument(
        "--refresh", action="store_true", help="fetch the schema again rather than use the cache"
    )
    parser.add_argument(
        "--offline",
        type=Path,
        metavar="SCHEMA.json",
        help="build from a saved schema; no rig needed for --help",
    )


def build_parser(schema: dict[str, Any] | None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flyball", description="Control a running flyball rig.")
    _global_options(parser)
    sub = parser.add_subparsers(dest="command_group", metavar="<command>")

    sub.add_parser("schema", help="the rig's schema, for saving or jq").set_defaults(fn=cmd_schema)
    sub.add_parser("signals", help="what the rig is waiting on").set_defaults(fn=cmd_signals)
    signal = sub.add_parser("signal", help="answer or cancel a wait")
    signal.add_argument("action", choices=["fire", "interrupt"])
    signal.add_argument("name")
    signal.set_defaults(fn=cmd_signal)
    watch = sub.add_parser("watch", help="follow a live stream as JSON lines")
    watch.add_argument("stream", choices=STREAMS)
    watch.set_defaults(fn=cmd_watch)
    sub.add_parser("clock", help="the rig's timebase").set_defaults(fn=cmd_clock)
    for kind in ("actuators", "readers", "sources", "loops"):
        sub.add_parser(kind, help=f"list the {kind}").set_defaults(fn=cmd_list, kind=kind)

    if schema is None:
        return parser
    for kind in ("actuators", "readers"):
        for name, device in schema.get(kind, {}).items():
            _add_device(sub, kind, name, device)
    return parser


def _add_device(sub: Any, kind: str, name: str, device: dict[str, Any]) -> None:
    summary = device.get("description") or device["type"]
    parser = sub.add_parser(name, help=f"{device['type']}: {summary}", description=summary)
    parser.set_defaults(fn=cmd_view, kind=kind, device=name)
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    commands.add_parser("schema", help="config, settings, state and command schemas").set_defaults(
        fn=cmd_device_schema
    )
    for tag, spec in device.get("commands", {}).items():
        arguments = spec["arguments"]
        defs = arguments.get("$defs", {})
        cmd = commands.add_parser(
            tag, help=spec.get("description") or tag, description=spec.get("description")
        )
        cmd.set_defaults(fn=cmd_run, command=tag)
        add_arguments(cmd, arguments, defs)
        # One argument: it may be given positionally -- ``set_flows 2 6``, ``set_flows 4``.
        properties = arguments.get("properties", {})
        if len(properties) == 1:
            (only,) = properties
            cmd.add_argument(
                "positional",
                nargs="*",
                type=_json_or_str,
                help=f"{only}, positionally",
                metavar=only.upper(),
            )
            for action in cmd._actions:
                if action.dest == only:
                    action.required = False


# endregion
# region Commands


def _out(args: argparse.Namespace, payload: Any) -> None:
    if args.json or not isinstance(payload, (dict, list)):
        print(json.dumps(payload))
    else:
        print(json.dumps(payload, indent=2))


def cmd_schema(rig: Rig, args: argparse.Namespace) -> None:
    _out(args, rig.schema)


def cmd_clock(rig: Rig, args: argparse.Namespace) -> None:
    _out(args, rig.clock())


def cmd_signals(rig: Rig, args: argparse.Namespace) -> None:
    _out(args, rig.signals())


def cmd_signal(rig: Rig, args: argparse.Namespace) -> None:
    done = rig.fire(args.name) if args.action == "fire" else rig.interrupt(args.name)
    _out(args, {"name": args.name, args.action: done})


def cmd_watch(rig: Rig, args: argparse.Namespace) -> None:
    try:
        for frame in rig.watch(args.stream):
            print(json.dumps(frame), flush=True)
    except BrokenPipeError:  # ``| head`` closed the pipe; that is the end, not an error
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


def cmd_list(rig: Rig, args: argparse.Namespace) -> None:
    if args.kind in ("actuators", "readers"):
        devices = rig.schema[args.kind]
        if args.json:
            _out(args, {n: d["type"] for n, d in devices.items()})
            return
        for name, device in devices.items():
            print(f"{name:16s} {device['type']:24s} {', '.join(device.get('commands', {})) or '-'}")
        return
    _out(args, rig.get(f"/api/{args.kind}"))


def cmd_view(rig: Rig, args: argparse.Namespace) -> None:
    _out(args, rig.get(f"/api/{args.kind}/{args.device}"))


def cmd_device_schema(rig: Rig, args: argparse.Namespace) -> None:
    _out(args, rig.schema[args.kind][args.device])


def cmd_run(rig: Rig, args: argparse.Namespace) -> None:
    body = body_from(args, _META | {"command_group"})
    positional = getattr(args, "positional", None)
    if positional:
        spec = rig.schema[args.kind][args.device]["commands"][args.command]
        (only,) = spec["arguments"]["properties"]
        if only in body:
            raise SchemaError(
                f"give {only} positionally or as --{only.replace('_', '-')}, not both"
            )
        body[only] = positional[0] if len(positional) == 1 else list(positional)
    device = getattr(rig, args.kind)[args.device]
    _out(args, device.run(args.command, **body))


# endregion


def main(argv: Sequence[str] | None = None) -> int:
    # Two passes: the global flags first, to know which rig's schema to build from.
    pre = argparse.ArgumentParser(add_help=False)
    _global_options(pre)
    pre_args, _ = pre.parse_known_args(argv)
    schema: dict[str, Any] | None = None
    try:
        schema, _ = load_schema(pre_args.url, pre_args.refresh, pre_args.offline)
    except Unreachable as e:
        cache = _cache_path(pre_args.url)
        if cache.exists():
            schema = json.loads(cache.read_text())  # stale is better than no --help at all
        else:
            print(f"flyball: {e}; no cached schema for this rig", file=sys.stderr)
    parser = build_parser(schema)
    args = parser.parse_args(argv)
    if not hasattr(args, "fn"):
        parser.print_help()
        return 0
    rig = Rig(args.url, timeout=args.timeout, schema=schema)
    try:
        args.fn(rig, args)
    except Unreachable as e:
        print(f"flyball: {e}", file=sys.stderr)
        return EXIT_UNREACHABLE
    except (RigError, SchemaError) as e:
        print(f"flyball: {e}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
