"""Command line for a running rig, built from the schema it publishes.

`flyball` fetches `/api/schema` once (cached under `~/.cache/flyball`) and
builds a subcommand per device and per command, so a new `@command` needs no
code here. Help text comes from the schema:

    flyball devices                         what is attached
    flyball pumps                           config, settings and state
    flyball pumps set_fraction --wet-fraction 0.25 --flow.tag absolute --flow.flow 8
    flyball pumps set_flows 2 6             a single argument may be positional
    flyball demand heaters.heater1 1200     PUT /api/signals/{address}
    flyball read furnace.zone1 --fresh      GET /api/read/{address}
    flyball waits                           what a program is waiting on
    flyball wait fire lid                   answer it
    flyball watch controllers               one JSON line per frame

Flags: `--name` per property, dotted for nested objects, `--flag/--no-flag`
for booleans, choices for enums. Any flag also takes a JSON literal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from flyball.client import Rig, RigError, SchemaError, Unreachable

DEFAULT_URL = "http://127.0.0.1:8000"
EXIT_ERROR = 1
EXIT_UNREACHABLE = 3
STREAMS = ("samples", "controllers", "writes", "signals")


# region Schema cache


def _cache_path(url: str) -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "flyball"
    return root / (hashlib.sha1(url.encode()).hexdigest()[:12] + ".json")


def load_schema(
    url: str, refresh: bool, offline: Path | None, token: str | None = None
) -> tuple[dict[str, Any], str]:
    """The rig's schema and where it came from: the rig, the cache, or a file."""
    if offline is not None:
        return json.loads(offline.read_text()), f"file {offline}"
    cache = _cache_path(url)
    if not refresh and cache.exists():
        try:
            return json.loads(cache.read_text()), "cache"
        except ValueError:
            pass
    schema = Rig(url, token=token).refresh()
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
    """`(value, label)` pairs for an enum or a oneOf of constants."""
    schema = _resolve(schema, defs)
    if "enum" in schema:
        return [(v, str(v)) for v in schema["enum"]]
    options = schema.get("oneOf") or schema.get("anyOf")
    if options and all("const" in _resolve(o, defs) for o in options):
        return [(_resolve(o, defs)["const"], _resolve(o, defs).get("title", "")) for o in options]
    return None


def _json_or_str(text: str) -> Any:
    """A flag value: JSON if it parses, else the text. `"4"` is 4, `"on"` is `"on"`."""
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
    """Nest `a.b.c` flags back into an object; drop what was not given."""
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
        help=f"runner base URL (env FLYBALL_URL, default {DEFAULT_URL})",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="seconds per request")
    parser.add_argument(
        "--token", default=None, help="bearer token the runner was started with (env FLYBALL_TOKEN)"
    )
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
    sub.add_parser("waits", help="what the rig is waiting on").set_defaults(fn=cmd_waits)
    wait = sub.add_parser("wait", help="answer or cancel a wait")
    wait.add_argument("action", choices=["fire", "interrupt"])
    wait.add_argument("name")
    wait.set_defaults(fn=cmd_wait)
    watch = sub.add_parser("watch", help="follow a live stream as JSON lines")
    watch.add_argument("stream", choices=STREAMS)
    watch.set_defaults(fn=cmd_watch)
    sub.add_parser("clock", help="the rig's timebase").set_defaults(fn=cmd_clock)
    sub.add_parser("status", help="one screen: devices, controllers, waits").set_defaults(
        fn=cmd_status
    )
    demand = sub.add_parser("demand", help="set a writable signal: PUT /api/signals/{address}")
    demand.add_argument("address")
    demand.add_argument("value", type=_json_or_str)
    demand.set_defaults(fn=cmd_demand)
    read = sub.add_parser("read", help="a signal, namespace or device: GET /api/read/{address}")
    read.add_argument("address")
    read.add_argument("--fresh", action="store_true", help="force a hardware read")
    read.set_defaults(fn=cmd_read)
    rig_cmd = sub.add_parser("rig", help="rig files: check one, or print their schema")
    rig_sub = rig_cmd.add_subparsers(dest="rig_action", metavar="<action>")
    check = rig_sub.add_parser(
        "check", help="validate one or more rig files, layered, without a rig"
    )
    check.add_argument("paths", type=Path, nargs="+", metavar="PATH")
    check.add_argument(
        "--set",
        dest="sets",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="override a value after loading, e.g. devices.furnace.config.noise=0.3; repeatable",
    )
    check.add_argument(
        "--print",
        action="store_true",
        help="print the merged document, in the first file's format",
    )
    check.set_defaults(fn=cmd_rig_check, local=True)
    rig_sub.add_parser("schema", help="the rig file's JSON schema, for an editor").set_defaults(
        fn=cmd_rig_schema, local=True
    )
    password = sub.add_parser(
        "password", help="hash a password for the runner's config (runner.auth.password)"
    )
    password.add_argument(
        "password", nargs="?", help="the password; default: asked for, without echo"
    )
    password.set_defaults(fn=cmd_password, local=True)
    program = sub.add_parser("program", help="program files")
    program_sub = program.add_subparsers(dest="program_action", metavar="<action>")
    pcheck = program_sub.add_parser(
        "check", help="normalise and validate a program file against the rig's commands"
    )
    pcheck.add_argument("path", type=Path)
    pcheck.add_argument(
        "--local",
        action="store_true",
        help="check against the commands installed here instead of asking the rig",
    )
    pcheck.set_defaults(fn=cmd_program_check)
    program_sub.add_parser(
        "schema", help="the program file's JSON schema, for an editor"
    ).set_defaults(fn=cmd_program_schema, local=True)
    prun = program_sub.add_parser("run", help="start a program file on the rig")
    prun.add_argument("path", type=Path)
    prun.add_argument("--interrupt", action="store_true", help="stop whatever is running first")
    prun.set_defaults(fn=cmd_program_run)
    program_sub.add_parser("status", help="what the programmer is doing").set_defaults(
        fn=cmd_program_status
    )
    program_sub.add_parser("stop", help="interrupt the running program").set_defaults(
        fn=cmd_program_stop
    )
    sim = sub.add_parser("sim", help="a simulated rig's knobs: clock speed, plants, saving")
    sim_sub = sim.add_subparsers(dest="sim_action", metavar="<action>")
    sim.set_defaults(fn=cmd_sim_show)
    sim_sub.add_parser("show", help="the clock and every plant").set_defaults(fn=cmd_sim_show)
    speed = sim_sub.add_parser("clock", help="run the rig's time at SPEED times wall time")
    speed.add_argument("speed", type=float, help="rig seconds per wall second, e.g. 60")
    speed.set_defaults(fn=cmd_sim_clock)
    step = sim_sub.add_parser("step", help="advance a stepped clock")
    step.add_argument("seconds", type=float)
    step.set_defaults(fn=cmd_sim_step)
    sset = sim_sub.add_parser("set", help="change a plant's parameters while it runs")
    sset.add_argument("plant", help="the link's name")
    sset.add_argument("parameters", nargs="+", metavar="KEY=VALUE", help="e.g. tau_s=30 noise=0.2")
    sset.set_defaults(fn=cmd_sim_set)
    reset = sim_sub.add_parser("reset", help="put a plant at an output")
    reset.add_argument("plant")
    reset.add_argument("output", type=float)
    reset.add_argument("--input", type=float)
    reset.set_defaults(fn=cmd_sim_reset)
    sim_sub.add_parser("config", help="the rig file as it now stands").set_defaults(
        fn=cmd_sim_config
    )
    save = sim_sub.add_parser("save", help="write the current config to the rig file")
    save.add_argument("path", nargs="?", help="elsewhere; the suffix picks the format")
    save.set_defaults(fn=cmd_sim_save)
    new = sub.add_parser("new", help="write a starting point for a device driver of your own")
    new.add_argument("name", help="the driver tag and file name: 'chiller', 'lab-probe'")
    new.add_argument("--dir", type=Path, default=Path("."), help="where to write it")
    new.set_defaults(fn=cmd_new, local=True)
    export = sub.add_parser("export", help="a recorded session as Bluesky event-model documents")
    export.add_argument("session", type=int, help="the session id (see `flyball sessions`)")
    export.add_argument("--out", type=Path, help="write JSON lines here instead of stdout")
    export.set_defaults(fn=cmd_export)
    sub.add_parser("sessions", help="list recorded sessions").set_defaults(fn=cmd_sessions)
    for kind in ("devices", "controllers"):
        sub.add_parser(kind, help=f"list the {kind}").set_defaults(fn=cmd_list, kind=kind)

    if schema is None:
        return parser
    for name, device in schema.get("devices", {}).items():
        _add_device(sub, name, device)
    return parser


def _add_device(sub: Any, name: str, device: dict[str, Any]) -> None:
    summary = device.get("description") or device["type"]
    parser = sub.add_parser(name, help=f"{device['type']}: {summary}", description=summary)
    parser.set_defaults(fn=cmd_view, device=name)
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
        # One argument: it may be given positionally -- `set_flows 2 6`, `set_flows 4`.
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


def _walk_signals(signals: Sequence[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Every leaf under a device's `signals` tree, depth-first: a namespace has no `access`."""
    for entry in signals:
        if "access" in entry:
            yield entry
        else:
            yield from _walk_signals(entry.get("signals", ()))


def cmd_status(rig: Rig, args: argparse.Namespace) -> None:
    """Everything at a glance, from the routes a dashboard would use."""
    health = rig.get("/api/health")
    if args.json:
        _out(args, health)
        return
    print(
        f"{'OK' if health['ok'] else 'ATTENTION'}  up {health['uptime_s']:.0f} s"
        f"  recording={'yes' if health['recording'] else 'no'}"
    )
    for device in rig.get("/api/devices"):
        flags = ", ".join(c["kind"] for c in device.get("conditions", ())) or "-"
        print(f"  device      {device['name']:16s} {device.get('driver', ''):20s} {flags}")
        for signal in _walk_signals(device.get("signals", ())):
            latest = signal.get("latest")
            write = signal.get("write")
            value = "" if latest is None else f"value={latest['value']!s:<10s}"
            demand = "" if write is None else f" write={write.get('value')!s}"
            print(f"    signal    {signal['address']:28s} [{signal['access']:>3s}] {value}{demand}")
    for controller in rig.get("/api/controllers"):
        reading = controller.get("reading") or {}
        print(
            f"  controller {controller['name']:16s} {controller['mode']:11s} "
            f"reading={reading.get('value')!s:10s} "
            f"setpoint={controller.get('setpoint')!s:10s} "
            f"law={(controller.get('law') or {}).get('tag', '-')}"
        )
    for name, signal in rig.waits().items():
        print(f"  waiting  {name:16s} {signal['outcome']:10s} {signal.get('message') or ''}")


def cmd_rig_check(rig: Rig, args: argparse.Namespace) -> None:
    """Validate one or more layered rig files. `--print` the canonical form, `--set` a change."""
    from flyball.core.config import discover
    from flyball.core.files import dumps
    from flyball.runtime.config import RigConfig, canonical, resolve_documents

    names = ", ".join(str(p) for p in args.paths)
    try:
        discover()
        document, files = resolve_documents(args.paths, args.sets)
        config = RigConfig.model_validate(document)
    except Exception as e:
        raise SchemaError(f"{names}: {e}") from None
    print(
        f"{names}: ok -- {config.name or 'unnamed'}: {len(config.links)} links, "
        f"{len(config.devices)} devices, {len(config.controllers)} controllers"
        + (f"; from {len(files)} files" if len(files) > 1 else "")
    )
    if args.print:
        print(dumps(canonical(config), args.paths[0].suffix), end="")


def cmd_rig_schema(rig: Rig, args: argparse.Namespace) -> None:
    from flyball.runtime.config import rig_schema

    _out(args, rig_schema())


def cmd_password(rig: Rig, args: argparse.Namespace) -> None:
    """Hash a password for `runner.auth.password`; nothing leaves this machine."""
    import getpass

    from flyball.server.auth import hash_password

    plain = args.password if args.password is not None else getpass.getpass("Password: ")
    if not plain:
        raise SchemaError("an empty password is no password")
    print(hash_password(plain))


def cmd_program_check(rig: Rig, args: argparse.Namespace) -> None:
    """The rig normalises and validates the file; nothing runs. `--local` does it here."""
    from flyball.core.files import load_document

    document = load_document(args.path)
    if args.local:
        from flyball.server.dialect import Dialect, normalise_program, program_from_document

        try:
            program_from_document(document, Dialect())
        except Exception as e:
            raise SchemaError(f"{args.path}: {e}") from e
        result = normalise_program(document, Dialect())
    else:
        result = rig.post("/api/programs/check", document)
    if args.json:
        _out(args, result)
        return
    print(f"{args.path}: ok -- {len(result['steps'])} steps")
    for i, step in enumerate(result["steps"]):
        command = step["command"]
        extra = {k: v for k, v in step.items() if k != "command"}
        print(
            f"  {i + 1:3d}. {command['command']:12s} "
            f"{json.dumps({k: v for k, v in command.items() if k != 'command'})}"
            f"{'  ' + json.dumps(extra) if extra else ''}"
        )


def _program_state(args: argparse.Namespace, state: dict[str, Any]) -> None:
    if args.json:
        _out(args, state)
    elif state["running"]:
        print(f"running: step {state['step'] + 1} of {state['steps']} ({state['command']})")
    else:
        print("idle")


def cmd_program_run(rig: Rig, args: argparse.Namespace) -> None:
    """Send the file's document; the rig normalises it and applies step one before answering."""
    from flyball.core.files import load_document

    query = "?interrupt=true" if args.interrupt else ""
    _program_state(args, rig.post(f"/api/programs/run{query}", load_document(args.path)))


def cmd_program_status(rig: Rig, args: argparse.Namespace) -> None:
    _program_state(args, rig.get("/api/programs/running"))


def cmd_program_stop(rig: Rig, args: argparse.Namespace) -> None:
    _program_state(args, rig.post("/api/programs/interrupt"))


def cmd_program_schema(rig: Rig, args: argparse.Namespace) -> None:
    from flyball.server.dialect import Dialect, program_schema

    _out(args, program_schema(Dialect()))


def _sim(rig: Rig) -> dict[str, Any]:
    state = rig.get("/api/sim")
    if not state.get("simulated"):
        raise SchemaError("this rig is not a simulation; `flyball sim` has nothing to adjust")
    return state


def cmd_sim_show(rig: Rig, args: argparse.Namespace) -> None:
    state = _sim(rig)
    if args.json:
        _out(args, state)
        return
    clock = state["clock"]
    measured = clock.get("measured")
    print(
        f"{state['name'] or 'rig'}: clock {clock['speed']:g}x"
        + (f" (measured {measured:.3g}x)" if measured else "")
        + f"{' (stepped)' if clock['stepped'] else ''}; file {state['path'] or '-'}"
    )
    for name, plant in state["plants"].items():
        config, live, links = plant["config"], plant.get("live", {}), plant.get("links", {})
        print(f"  {name} ({config.get('kind') or config.get('tag', 'plant')})")
        for key, value in config.items():
            if key in ("kind", "tag", "seed") or value is None:
                continue
            beside = live.get(key)
            if beside is not None:
                # A statistic is what was observed; anything else is where the quantity is now.
                word = "observed" if links.get(key, "").startswith("stats.") else "now"
                values = beside.values() if isinstance(beside, dict) else [beside]
                beside = f"  ({word} {' / '.join(_g(v) for v in values)})"
            print(f"    {key:24s} {_g(value)}{beside or ''}")
        rates = plant.get("stats", {}).get("rate_per_min", {})
        for port, reading in plant.get("readings", {}).items():
            rate = rates.get(port)
            print(
                f"    reading {port:16s} {_g(reading['value'])} {reading['unit']}"
                + (f"  {rate:+.3g}/min" if rate is not None else "")
                + f"  via {reading['device']}"
            )
    if state["changed"]:
        print(f"unsaved: {', '.join(state['changed'])}  (`flyball sim save`)")


def _g(value: Any) -> str:
    """A number to four significant figures; anything else as is."""
    return f"{value:.4g}" if isinstance(value, float) else str(value)


def cmd_sim_clock(rig: Rig, args: argparse.Namespace) -> None:
    _out(args, rig.put("/api/sim/clock", {"speed": args.speed}))


def cmd_sim_step(rig: Rig, args: argparse.Namespace) -> None:
    _out(args, rig.post("/api/sim/clock/step", {"seconds": args.seconds}))


def cmd_sim_set(rig: Rig, args: argparse.Namespace) -> None:
    parameters: dict[str, Any] = {}
    for item in args.parameters:
        key, sep, value = item.partition("=")
        if not sep:
            raise SchemaError(f"{item!r}: write KEY=VALUE")
        parameters[key] = _json_or_str(value)
    _out(args, rig.put(f"/api/sim/plants/{args.plant}", parameters))


def cmd_sim_reset(rig: Rig, args: argparse.Namespace) -> None:
    body = {"output": args.output, **({"input": args.input} if args.input is not None else {})}
    _out(args, rig.post(f"/api/sim/plants/{args.plant}/reset", body))


def cmd_sim_config(rig: Rig, args: argparse.Namespace) -> None:
    _out(args, rig.get("/api/sim/config"))


def cmd_sim_save(rig: Rig, args: argparse.Namespace) -> None:
    result = rig.post("/api/sim/save", {"path": args.path} if args.path else {})
    print(f"saved {result['path']}")


def cmd_new(rig: Rig, args: argparse.Namespace) -> None:
    from flyball.scaffold import write

    try:
        path = write(args.name, args.dir)
    except FileExistsError as e:
        raise SchemaError(f"{e} exists; not overwriting") from e
    except ValueError as e:
        raise SchemaError(str(e)) from e
    print(
        f"wrote {path}; add it to the rig's package and declare driver = {path.stem!r} in the file"
    )


def cmd_sessions(rig: Rig, args: argparse.Namespace) -> None:
    _out(args, rig.get("/api/sessions"))


def cmd_export(rig: Rig, args: argparse.Namespace) -> None:
    """One `{"name": ..., "doc": ...}` per line: what `bluesky` and databroker consume."""
    lines = [
        json.dumps({"name": name, "doc": doc})
        for name, doc in rig.get(f"/api/sessions/{args.session}/documents")
    ]
    if args.out is not None:
        args.out.write_text("\n".join(lines) + "\n")
        print(f"wrote {len(lines)} documents to {args.out}")
    else:
        print("\n".join(lines))


def cmd_waits(rig: Rig, args: argparse.Namespace) -> None:
    _out(args, rig.waits())


def cmd_wait(rig: Rig, args: argparse.Namespace) -> None:
    done = rig.fire(args.name) if args.action == "fire" else rig.interrupt(args.name)
    _out(args, {"name": args.name, args.action: done})


def cmd_demand(rig: Rig, args: argparse.Namespace) -> None:
    _out(args, rig.demand(args.address, args.value))


def cmd_read(rig: Rig, args: argparse.Namespace) -> None:
    _out(args, rig.read(args.address, fresh=args.fresh))


def cmd_watch(rig: Rig, args: argparse.Namespace) -> None:
    try:
        for frame in rig.watch(args.stream):
            print(json.dumps(frame), flush=True)
    except BrokenPipeError:  # `| head` closed the pipe; that is the end, not an error
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


def cmd_list(rig: Rig, args: argparse.Namespace) -> None:
    if args.kind == "devices":
        devices = rig.schema["devices"]
        if args.json:
            _out(args, {n: d["type"] for n, d in devices.items()})
            return
        for name, device in devices.items():
            print(f"{name:16s} {device['type']:24s} {', '.join(device.get('commands', {})) or '-'}")
        return
    _out(args, rig.get(f"/api/{args.kind}"))


def cmd_view(rig: Rig, args: argparse.Namespace) -> None:
    _out(args, rig.get(f"/api/devices/{args.device}"))


def cmd_device_schema(rig: Rig, args: argparse.Namespace) -> None:
    _out(args, rig.schema["devices"][args.device])


def cmd_run(rig: Rig, args: argparse.Namespace) -> None:
    body = body_from(args, _META | {"command_group"})
    positional = getattr(args, "positional", None)
    if positional:
        spec = rig.schema["devices"][args.device]["commands"][args.command]
        (only,) = spec["arguments"]["properties"]
        if only in body:
            raise SchemaError(
                f"give {only} positionally or as --{only.replace('_', '-')}, not both"
            )
        body[only] = positional[0] if len(positional) == 1 else list(positional)
    device = rig.devices[args.device]
    _out(args, device.run(args.command, **body))


# endregion


def main(argv: Sequence[str] | None = None) -> int:
    # Two passes: the global flags first, to know which rig's schema to build from.
    pre = argparse.ArgumentParser(add_help=False)
    _global_options(pre)
    pre_args, rest = pre.parse_known_args(argv)
    # rig check/schema, program schema, program check --local and new need no rig.
    local = bool(rest) and (
        rest[0] in ("rig", "new", "password")
        or (rest[0] == "program" and "--local" in rest)
        or rest[:2] == ["program", "schema"]
    )
    schema: dict[str, Any] | None = None
    try:
        if not local:
            schema, _ = load_schema(
                pre_args.url, pre_args.refresh, pre_args.offline, pre_args.token
            )
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
    rig = Rig(args.url, timeout=args.timeout, schema=schema, token=args.token)
    try:
        if getattr(args, "local", False) and schema is None:
            schema = {}  # a local command: no rig needed
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
