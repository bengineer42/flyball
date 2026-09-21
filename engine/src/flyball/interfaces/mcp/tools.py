"""The tools, each a tier and one call on the client.

A tool's schema is what the model sees; a device command's comes from the
rig's own `/api/schema`, so its limits and units are this instance's. The
mode chooses a tier and every tool at or below it is listed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any

from flyball.interfaces.client import Rig, RigError, SchemaError
from flyball.scaffold import render

__all__ = ["GUIDES", "MODES", "Tier", "Tool", "tools_for"]


class Tier(IntEnum):
    READ = 0
    """Nothing changes anywhere; a check that validates and returns is a read."""
    AUTHOR = 1
    """Writes the store -- programs, dashboards, tunings -- never the hardware."""
    DRIVE = 2
    """The running rig: commands, demands, controllers, programs, recording."""


MODES = {"read": Tier.READ, "author": Tier.AUTHOR, "operate": Tier.DRIVE}

Run = Callable[[Rig, dict[str, Any]], Any]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    tier: Tier
    run: Run
    destructive: bool = False
    """Interrupts or removes something: a controller goes to manual, a version is deleted."""
    route: tuple[str, str] | None = None
    """(method, path) the runner must serve for this tool to be listed; see `MCP.md`."""
    changes_tools: bool = False
    """Attaches or detaches devices: the tool list is rebuilt and clients told."""


# region Schema shorthands


def _object(properties: dict[str, Any] | None = None, *required: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": list(required),
        "additionalProperties": False,
    }


def _str(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "string", "description": description, **extra}


def _num(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "number", "description": description, **extra}


def _int(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "integer", "description": description, **extra}


def _bool(description: str, default: bool = False) -> dict[str, Any]:
    return {"type": "boolean", "description": description, "default": default}


def _any(description: str) -> dict[str, Any]:
    return {"description": description}


NAME = _str("The name.")
ADDRESS = _str("A signal address, e.g. `blender.flows.dry`.")
DOCUMENT = {"type": "object", "description": "The document, as JSON."}
FRESH = _bool("Read the device now rather than answer from the latest reading.")
SESSION = _int("A recorded session's id, from `list_sessions`.")

# endregion
# region Read


def _query(**params: Any) -> str:
    given = {k: v for k, v in params.items() if v is not None}
    return "?" + "&".join(f"{k}={v}" for k, v in given.items()) if given else ""


READ: tuple[Tool, ...] = (
    Tool(
        "status",
        "One look at the rig: ok or not, uptime, devices polling, controller modes, conditions, "
        "alarms, waits, recording.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.get("/api/health"),
    ),
    Tool(
        "list_devices",
        "Every device: name, type, label and a one-line description. `describe_device` for one.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.get("/api/devices"),
    ),
    Tool(
        "describe_device",
        "A device's schema: every signal with its unit, limits and access, its inputs, and its "
        "commands with their argument schemas. What to read before writing a program or a "
        "dashboard that names its signals.",
        _object({"name": NAME}, "name"),
        Tier.READ,
        lambda rig, a: rig.get(f"/api/devices/{a['name']}/schema"),
    ),
    Tool(
        "view_device",
        "A device now: its tree with current values, inputs, commands and conditions.",
        _object({"name": NAME}, "name"),
        Tier.READ,
        lambda rig, a: rig.get(f"/api/devices/{a['name']}"),
    ),
    Tool(
        "read",
        "The latest reading of a signal, the sample of a namespace, or every sample of a device.",
        _object({"address": ADDRESS, "fresh": FRESH}, "address"),
        Tier.READ,
        lambda rig, a: rig.read(a["address"], bool(a.get("fresh", False))),
    ),
    Tool(
        "read_many",
        "Several addresses in one call, in the order given.",
        _object(
            {"addresses": {"type": "array", "items": ADDRESS, "minItems": 1}, "fresh": FRESH},
            "addresses",
        ),
        Tier.READ,
        lambda rig, a: rig.get(
            "/api/read" + _query(at=",".join(a["addresses"]), fresh=a.get("fresh") or None)
        ),
    ),
    Tool(
        "events",
        "The rig's latest events, newest first: alarms, interrupts, program steps, errors.",
        _object({
            "limit": _int("At most this many.", default=100, minimum=1, maximum=1000),
            "level": _str("This level and above.", enum=["DEBUG", "INFO", "WARNING", "ERROR"]),
        }),
        Tier.READ,
        lambda rig, a: rig.get("/api/events" + _query(limit=a.get("limit"), level=a.get("level"))),
    ),
    Tool(
        "clock",
        "The rig's timebase: start, now, elapsed, and its speed if simulated.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.clock(),
    ),
    Tool(
        "controllers",
        "Every controller: the signal it drives, the one it reads, its mode, setpoint and tuning.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.controllers(),
    ),
    Tool(
        "waits",
        "What a running program is waiting on, by name; `fire` answers one.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.waits(),
    ),
    Tool(
        "program_status",
        "What the programmer is doing: the program, its step and progress.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.get("/api/programs/running"),
    ),
    Tool(
        "list_programs",
        "The program library: the newest version of each, with its format.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.get("/api/programs/library"),
    ),
    Tool(
        "get_program",
        "A stored program's newest version: its text and format.",
        _object({"name": NAME}, "name"),
        Tier.READ,
        lambda rig, a: rig.get(f"/api/programs/library/{a['name']}"),
    ),
    Tool(
        "check_program",
        "Validate a program document against the rig: nothing runs. Returns the normalised "
        "form and, per step, anything it names that the rig lacks.",
        _object({"document": DOCUMENT}, "document"),
        Tier.READ,
        lambda rig, a: rig.post("/api/programs/check", a["document"]),
    ),
    Tool(
        "program_schema",
        "JSON schema of a program document: the steps and what each takes.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.get("/api/programs/schema"),
    ),
    Tool(
        "command_schema",
        "JSON schema of every program command, discriminated by `command`.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.get("/api/programs/commands"),
    ),
    Tool(
        "tunings",
        "The control-law tunings loaded on the rig, by tag.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.get("/api/tunings"),
    ),
    Tool(
        "saved_tunings",
        "Tunings saved to the store, newest version of each.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.get("/api/history/tunings"),
    ),
    Tool(
        "list_sessions",
        "Recorded sessions, newest first.",
        _object({"limit": _int("At most this many.", minimum=1)}),
        Tier.READ,
        lambda rig, a: rig.get("/api/history/sessions" + _query(limit=a.get("limit"))),
    ),
    Tool(
        "session",
        "One recorded session: when, what was recorded, its config.",
        _object({"session_id": SESSION}, "session_id"),
        Tier.READ,
        lambda rig, a: rig.get(f"/api/history/sessions/{a['session_id']}"),
    ),
    Tool(
        "session_series",
        "One signal over a recorded session, averaged to at most `max_points`.",
        _object(
            {
                "session_id": SESSION,
                "address": ADDRESS,
                "start_ns": _int("Window start, rig time in ns; default the session start."),
                "end_ns": _int("Window end; default the session end."),
                "max_points": _int("Average into at most this many.", default=200, minimum=2),
            },
            "session_id",
            "address",
        ),
        Tier.READ,
        lambda rig, a: rig.get(
            f"/api/history/sessions/{a['session_id']}/series/{a['address']}"
            + _query(
                start_ns=a.get("start_ns"),
                end_ns=a.get("end_ns"),
                max_points=a.get("max_points", 200),
            )
        ),
    ),
    Tool(
        "list_dashboards",
        "Saved dashboards for this rig.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.get("/api/dashboards"),
    ),
    Tool(
        "get_dashboard",
        "A dashboard's newest version, with `problems`: widgets bound to things the rig lacks.",
        _object({"name": NAME}, "name"),
        Tier.READ,
        lambda rig, a: rig.get(f"/api/dashboards/{a['name']}"),
    ),
    Tool(
        "dashboard_schema",
        "JSON schema of a dashboard document: the grid and the widget envelope. "
        "`widget_schema` says what each kind's `config` holds.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.get("/api/dashboards/schema"),
    ),
    Tool(
        "widget_schema",
        "Every widget kind: what it shows, its default and minimum size, and its `config` "
        "schema. An `x-binding` marks a property that takes a signal address, a controller's "
        "name or a device's name from this rig.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.get("/api/dashboards/widgets"),
    ),
    Tool(
        "rig_schema",
        "JSON schema of a rig file, with every driver the runner has installed.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.get("/api/rig/schema"),
    ),
    Tool(
        "rig_config",
        "The rig file as it now stands.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.get("/api/rig/config"),
    ),
    Tool(
        "check_rig",
        "Validate a rig document against the runner's drivers: nothing is built. Returns the "
        "canonical form, or what is wrong.",
        _object({"document": DOCUMENT}, "document"),
        Tier.READ,
        lambda rig, a: rig.post("/api/rig/check", a["document"]),
    ),
    Tool(
        "simulation",
        'A simulated rig\'s clock and plants; `{"simulated": false}` on hardware.',
        _object(),
        Tier.READ,
        lambda rig, a: rig.sim(),
    ),
)

# endregion
# region Author


def _save_program(rig: Rig, a: dict[str, Any]) -> Any:
    if "document" in a:
        fmt, body = "json", json.dumps(a["document"], indent=2)
    elif "text" in a:
        fmt, body = a.get("format", "yaml"), a["text"]
    else:
        raise SchemaError("save_program: give `document` (JSON) or `text` (with `format`)")
    envelope = {"format": fmt, "body": body, "label": a.get("label"), "notes": a.get("notes")}
    return rig.put(f"/api/programs/library/{a['name']}", envelope)


def _apply(document: dict[str, Any], change: dict[str, Any]) -> None:
    """One change to a dashboard document, in place."""
    widgets: list[dict[str, Any]] = document.setdefault("widgets", [])
    op = change["op"]
    if op == "add_widget":
        widgets.append(change["widget"])
        return
    if op == "set_description":
        document["description"] = change["description"]
        return
    matches = [w for w in widgets if w.get("id") == change["id"]]
    if not matches:
        raise SchemaError(
            f"no widget {change['id']!r}; the dashboard has {[w.get('id') for w in widgets]}"
        )
    widget = matches[0]
    if op == "remove_widget":
        widgets.remove(widget)
    elif op == "move_widget":
        widget.update({k: change[k] for k in ("x", "y", "w", "h") if k in change})
    elif op == "set_widget_config":
        widget["config"] = {**widget.get("config", {}), **change["config"]}
    elif op == "set_widget_title":
        widget["title"] = change["title"]
    else:
        raise SchemaError(f"unknown op {op!r}")


def _update_dashboard(rig: Rig, a: dict[str, Any]) -> Any:
    document = rig.get(f"/api/dashboards/{a['name']}")["body"]
    for change in a["changes"]:
        _apply(document, change)
    return rig.put(f"/api/dashboards/{a['name']}", document)


WIDGET = {
    "type": "object",
    "description": "A widget: `id`, `kind`, `x`, `y`, `w`, `h`, optional `title`, and the kind's "
    "`config` (see `widget_schema`).",
}
CHANGE = {
    "type": "object",
    "description": "One change. `op` is one of: `add_widget` (with `widget`); `remove_widget`, "
    "`move_widget` (any of `x`, `y`, `w`, `h`), `set_widget_config` (merged into `config`), "
    "`set_widget_title` (with `id`); `set_description`.",
    "properties": {
        "op": {
            "type": "string",
            "enum": [
                "add_widget",
                "remove_widget",
                "move_widget",
                "set_widget_config",
                "set_widget_title",
                "set_description",
            ],
        },
        "id": _str("The widget, for every op but `add_widget` and `set_description`."),
        "widget": WIDGET,
        "x": _int(""),
        "y": _int(""),
        "w": _int(""),
        "h": _int(""),
        "config": {"type": "object"},
        "title": _str(""),
        "description": _str(""),
    },
    "required": ["op"],
}

AUTHOR: tuple[Tool, ...] = (
    Tool(
        "save_program",
        "Save a program to the library as a new version under `name`: `document` (JSON) or "
        "`text` in `format`. Stored as sent; `check_program` first.",
        _object(
            {
                "name": NAME,
                "document": DOCUMENT,
                "text": _str("The program as written, when not giving `document`."),
                "format": _str("What `text` is written in.", enum=["yaml", "toml", "json"]),
                "label": _str("A label for this version."),
                "notes": _any("Free-form notes on this version."),
            },
            "name",
        ),
        Tier.AUTHOR,
        _save_program,
    ),
    Tool(
        "rename_program",
        "Move a program, every version, under a new name.",
        _object({"name": NAME, "new_name": _str("The new name.")}, "name", "new_name"),
        Tier.AUTHOR,
        lambda rig, a: rig.post(
            f"/api/programs/library/{a['name']}/rename", {"name": a["new_name"]}
        ),
    ),
    Tool(
        "delete_program",
        "Delete a program and its whole history.",
        _object({"name": NAME}, "name"),
        Tier.AUTHOR,
        lambda rig, a: rig.delete(f"/api/programs/library/{a['name']}"),
        destructive=True,
    ),
    Tool(
        "save_dashboard",
        "Save a dashboard document as a new version under `name`. The grid is 24 columns; "
        "`widget_schema` gives each kind's size and config. Returns `problems`: widgets bound to "
        "things the rig lacks.",
        _object({"name": NAME, "document": DOCUMENT}, "name", "document"),
        Tier.AUTHOR,
        lambda rig, a: rig.put(f"/api/dashboards/{a['name']}", a["document"]),
    ),
    Tool(
        "update_dashboard",
        "Change a dashboard by its parts -- add, remove, move or reconfigure widgets -- and save "
        "the result as a new version. Returns the saved document with `problems`.",
        _object(
            {"name": NAME, "changes": {"type": "array", "items": CHANGE, "minItems": 1}},
            "name",
            "changes",
        ),
        Tier.AUTHOR,
        _update_dashboard,
    ),
    Tool(
        "rename_dashboard",
        "Move a dashboard, every version, under a new name.",
        _object({"name": NAME, "new_name": _str("The new name.")}, "name", "new_name"),
        Tier.AUTHOR,
        lambda rig, a: rig.post(f"/api/dashboards/{a['name']}/rename", {"name": a["new_name"]}),
    ),
    Tool(
        "delete_dashboard",
        "Delete a dashboard and its whole history.",
        _object({"name": NAME}, "name"),
        Tier.AUTHOR,
        lambda rig, a: rig.delete(f"/api/dashboards/{a['name']}"),
        destructive=True,
    ),
    Tool(
        "save_tuning",
        "Save a control-law config to the store under `name`; `apply_tuning` puts one on the rig.",
        _object(
            {
                "name": NAME,
                "law": _str("The law's tag, e.g. `pid`."),
                "config": {"type": "object", "description": "The law's config."},
                "notes": _any("Free-form notes."),
            },
            "name",
            "law",
            "config",
        ),
        Tier.AUTHOR,
        lambda rig, a: rig.put(
            f"/api/history/tunings/{a['name']}",
            {
                "law": a["law"],
                "config": a["config"],
                "notes": a.get("notes"),
                "created_ns": rig.clock()["now_ns"],
            },
        ),
    ),
)

# endregion
# region Drive

AT = _any(
    "Where to aim: a number; `process`, `setpoint` or `demand` for the current one; or a "
    "generator config (a ramp, say) as an object."
)
TARGET = _str("The controller: the address of the signal it drives.")
WAIT = _str("The wait's name, from `waits`.")

DRIVE: tuple[Tool, ...] = (
    Tool(
        "set_demand",
        "Put a value on one writable signal. Refused while a controller drives it.",
        _object(
            {"address": ADDRESS, "value": _num("The value, in the signal's unit.")},
            "address",
            "value",
        ),
        Tier.DRIVE,
        lambda rig, a: rig.demand(a["address"], a["value"]),
    ),
    Tool(
        "regulate",
        "Aim a controller and hand it control.",
        _object(
            {
                "target": TARGET,
                "at": AT,
                "start": _any("Where a generator starts from; default the current setpoint."),
                "tuning": _any(
                    "A tuning's tag, or a law config as an object; default the current."
                ),
                "transfer": _str(
                    "How the law takes over.", enum=["none", "carry", "track", "reset"]
                ),
            },
            "target",
            "at",
        ),
        Tier.DRIVE,
        lambda rig, a: rig.post(
            f"/api/controllers/{a['target']}/regulate",
            {k: v for k, v in a.items() if k != "target"},
        ),
    ),
    Tool(
        "manual",
        "Put a controller in manual: it stops driving; the demand stays where it is.",
        _object({"target": TARGET}, "target"),
        Tier.DRIVE,
        lambda rig, a: rig.post(f"/api/controllers/{a['target']}/manual"),
    ),
    Tool(
        "set_reference",
        "Move a regulating controller's target without changing anything else.",
        _object(
            {"target": TARGET, "at": AT, "start": _any("Where a generator starts from.")},
            "target",
            "at",
        ),
        Tier.DRIVE,
        lambda rig, a: rig.put(
            f"/api/controllers/{a['target']}/reference",
            {k: v for k, v in a.items() if k != "target"},
        ),
    ),
    Tool(
        "make_controller",
        "Make a controller: a writable signal to drive, a signal to regulate, and a law.",
        _object(
            {
                "target": TARGET,
                "source": _str("The signal to regulate."),
                "law": _any("A tuning's tag or a law config; default the rig's default law."),
                "feedforward": _any("A feedforward config or tuning tag, if any."),
            },
            "target",
            "source",
        ),
        Tier.DRIVE,
        lambda rig, a: rig.post("/api/controllers", a),
    ),
    Tool(
        "remove_controller",
        "Remove a controller; its demand stays where it is.",
        _object({"target": TARGET}, "target"),
        Tier.DRIVE,
        lambda rig, a: rig.delete(f"/api/controllers/{a['target']}"),
        destructive=True,
    ),
    Tool(
        "apply_tuning",
        "Put a control-law config on the rig under `tag`, for controllers to use by name.",
        _object(
            {
                "tag": _str("The tuning's tag."),
                "law": {"type": "object", "description": "The law config, with its `tag`."},
            },
            "tag",
            "law",
        ),
        Tier.DRIVE,
        lambda rig, a: rig.put(f"/api/tunings/{a['tag']}", a["law"]),
    ),
    Tool(
        "run_program",
        "Start a program: a stored one by `name`, or a `document`. Refused while one runs "
        "unless `interrupt`.",
        _object({
            "name": _str("A stored program."),
            "document": DOCUMENT,
            "interrupt": _bool("Stop whatever is running first."),
        }),
        Tier.DRIVE,
        lambda rig, a: rig.post(
            (f"/api/programs/library/{a['name']}/run" if "name" in a else "/api/programs/run")
            + _query(interrupt="true" if a.get("interrupt") else None),
            None if "name" in a else a.get("document"),
        ),
        destructive=True,
    ),
    Tool(
        "interrupt_program",
        "Stop the running program.",
        _object(),
        Tier.DRIVE,
        lambda rig, a: rig.post("/api/programs/interrupt"),
        destructive=True,
    ),
    Tool(
        "fire",
        "Answer a wait: the program goes on.",
        _object({"name": WAIT}, "name"),
        Tier.DRIVE,
        lambda rig, a: {"fired": rig.fire(a["name"])},
    ),
    Tool(
        "interrupt_wait",
        "Cancel a wait: the program is interrupted.",
        _object({"name": WAIT}, "name"),
        Tier.DRIVE,
        lambda rig, a: {"interrupted": rig.interrupt(a["name"])},
        destructive=True,
    ),
    Tool(
        "start_recording",
        "Open a recorded session.",
        _object({"details": _any("A name, notes, tags: anything to note about the session.")}),
        Tier.DRIVE,
        lambda rig, a: rig.post("/api/recording", {"details": a.get("details")}),
    ),
    Tool(
        "end_recording",
        "Close the open session.",
        _object(),
        Tier.DRIVE,
        lambda rig, a: rig.post("/api/recording/end"),
    ),
    Tool(
        "restart_device",
        "Restart a device's polling after a fault.",
        _object({"name": NAME}, "name"),
        Tier.DRIVE,
        lambda rig, a: rig.post(f"/api/devices/{a['name']}/restart"),
    ),
)

SIM: tuple[Tool, ...] = (
    Tool(
        "sim_clock",
        "Run the simulated rig's time at `speed` times wall time.",
        _object({"speed": _num("Rig seconds per wall second.", exclusiveMinimum=0)}, "speed"),
        Tier.DRIVE,
        lambda rig, a: rig.put("/api/sim/clock", {"speed": a["speed"]}),
    ),
    Tool(
        "sim_step",
        "Advance a stepped clock by `seconds`.",
        _object({"seconds": _num("", exclusiveMinimum=0)}, "seconds"),
        Tier.DRIVE,
        lambda rig, a: rig.post("/api/sim/clock/step", {"seconds": a["seconds"]}),
    ),
    Tool(
        "sim_set_plant",
        "Change some of a plant's parameters while it runs.",
        _object({"name": NAME, "parameters": {"type": "object"}}, "name", "parameters"),
        Tier.DRIVE,
        lambda rig, a: rig.put(f"/api/sim/plants/{a['name']}", a["parameters"]),
    ),
    Tool(
        "sim_reset_plant",
        "Put a plant at an output and/or input, at once.",
        _object({"name": NAME, "output": _num(""), "input": _num("")}, "name"),
        Tier.DRIVE,
        lambda rig, a: rig.post(
            f"/api/sim/plants/{a['name']}/reset",
            {k: a.get(k) for k in ("output", "input")},
        ),
    ),
)


def _command(name: str, tag: str) -> Run:
    return lambda rig, a: rig.devices[name].run(tag, **a)


def _device_tools(rig: Rig, simulated: bool) -> list[Tool]:
    """One tool per device command, `<device>-<command>`, from the rig's schema."""
    tools = []
    for name, device in rig.schema["devices"].items():
        for tag, spec in device["commands"].items():
            if spec.get("simulation") and not simulated:
                continue
            notes = []
            if spec.get("mode") is not None:
                notes.append(f"Puts the device in mode `{spec['mode']}`.")
            if spec.get("interrupts"):
                notes.append("A controller driving this device goes to manual first.")
            if spec.get("simulation"):
                notes.append("A simulation-only command.")
            description = " ".join([spec.get("description") or f"{name}.{tag}", *notes])
            tools.append(
                Tool(
                    f"{name}-{tag}",
                    f"[{device.get('label') or name}] {description}",
                    spec["arguments"],
                    Tier.DRIVE,
                    _command(name, tag),
                    destructive=bool(spec.get("interrupts")),
                )
            )
    return tools


# endregion
# region Drivers: new equipment, as a config entry or as code
#
# Most instruments need no code: the generic `scpi` and `modbus` drivers take
# their signals from the rig-file entry. `probe_hardware` and `link_query`
# find out what is there; `attach_device` puts an entry on the running rig.
# Equipment that needs code gets the guide, a scaffold, a checker that
# imports the file where this server runs, and `reload_drivers` for a
# directory the runner loads from. A tool whose route the runner does not
# serve is not listed. Building on a hardware rig is the runner's
# `--compose` opt-in: without it the attach tools are refused with 409.

GUIDES = Path(__file__).parent / "guides"

_CHECK = """\
import importlib.util, json, sys
from pathlib import Path
path = Path(sys.argv[1])
out = {"path": str(path), "ok": False, "drivers": [], "errors": []}
try:
    from flyball.foundation.device import DriverConfig
    from flyball.model.config import Config
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    tags = sorted(
        value.config_tag
        for value in vars(module).values()
        if isinstance(value, type)
        and issubclass(value, Config)
        and value.__module__ == path.stem
        and value.config_tag is not None
    )
    for tag in tags:
        config = next(
            v for v in vars(module).values()
            if isinstance(v, type) and issubclass(v, Config) and v.config_tag == tag
        )
        if not issubclass(config, DriverConfig):
            continue
        entry = {"tag": tag, "config": config.__name__}
        try:
            entry["schema"] = config.model_json_schema()
            generic = [
                m for b in config.__mro__
                if (m := getattr(b, "__pydantic_generic_metadata__", None)) and m.get("args")
            ]
            device = generic[0]["args"][0] if generic else None
            if isinstance(device, type):
                entry["device"] = device.__name__
                entry["readable"] = getattr(device, "readable", None)
                entry["writable"] = getattr(device, "writable", None)
                entry["descriptors"] = sorted(getattr(device, "DESCRIPTORS", {}) or [])
                entry["commands"] = sorted(getattr(device, "commands", {}) or [])
        except Exception as e:
            out["errors"].append(f"{tag}: {type(e).__name__}: {e}")
        out["drivers"].append(entry)
    if not out["drivers"]:
        out["errors"].append("the module registers no DriverConfig subclass with a tag")
    out["ok"] = not out["errors"]
except Exception as e:
    out["errors"].append(f"{type(e).__name__}: {e}")
print(json.dumps(out))
"""


def _check_driver(rig: Rig, a: dict[str, Any]) -> Any:
    """Import a driver module in a fresh interpreter, where this server runs, and report."""
    path = Path(a["path"]).expanduser()
    if not path.is_file():
        raise SchemaError(f"check_driver: {path} is not a file here")
    run = subprocess.run(
        [sys.executable, "-c", _CHECK, str(path)], capture_output=True, text=True, timeout=60
    )
    if run.returncode != 0 or not run.stdout.strip():
        return {"path": str(path), "ok": False, "errors": [run.stderr.strip()[-2000:]]}
    return json.loads(run.stdout)


def _search_drivers(rig: Rig, a: dict[str, Any]) -> Any:
    """Run `linux/scripts/search_drivers.py --json <filters>` and parse its output.

    A `linux/` checkout, not a running rig, holds the catalogue -- this tool doesn't touch
    `rig` at all, matching `driver_scaffold`/`check_driver`'s local-filesystem shape rather
    than `list_drivers`' HTTP-to-a-running-runner one.
    """
    linux_dir = Path(a["linux_dir"]).expanduser()
    script = linux_dir / "scripts" / "search_drivers.py"
    if not script.is_file():
        raise SchemaError(f"search_drivers: {script} is not a file here")
    flags = ["--json"]
    for key in (
        "tag",
        "category",
        "interface",
        "tier",
        "status",
        "manufacturer",
        "domain",
        "unit",
        "dimension",
        "text",
    ):
        if (value := a.get(key)) is not None:
            flags += [f"--{key}", str(value)]
    # Not `sys.executable`: the manifest search needs PyYAML, which is `linux/`'s own
    # dependency, not this server's -- `uv run` resolves it from `linux_dir`'s own venv.
    run = subprocess.run(
        ["uv", "run", "--", "python", str(script), *flags],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=linux_dir,
    )
    if run.returncode != 0:
        raise SchemaError(f"search_drivers: {run.stderr.strip()[-2000:]}")
    return json.loads(run.stdout)


def _scaffold(rig: Rig, a: dict[str, Any]) -> Any:
    try:
        return {"name": a["name"], "source": render(a["name"])}
    except ValueError as e:
        raise SchemaError(str(e)) from e


DRIVERS: tuple[Tool, ...] = (
    Tool(
        "driver_guide",
        "How to write a device driver for this rig: descriptors, read and commit, commands, "
        "the config that registers a tag, links, and how a driver is checked and attached. "
        "Read before writing one; most instruments need only a config entry, which it says.",
        _object(),
        Tier.READ,
        lambda rig, a: (GUIDES / "driver.md").read_text(),
    ),
    Tool(
        "driver_scaffold",
        "A complete starting module for a driver called `name`: it imports, registers the tag "
        "and works in a rig file before a line is changed. What `flyball new NAME` writes.",
        _object({"name": _str("The driver's tag; a Python identifier is made from it.")}, "name"),
        Tier.READ,
        _scaffold,
    ),
    Tool(
        "check_driver",
        "Import a driver module from a file on the machine this server runs on, in a fresh "
        "interpreter, and report: the tags it registers, each config's schema, the device's "
        "signals and commands, or what went wrong. Runs the file's top level.",
        _object({"path": _str("The module's path, where this server runs.")}, "path"),
        Tier.DRIVE,
        _check_driver,
    ),
    Tool(
        "search_drivers",
        "Search the hardware catalogue -- part number, manufacturer, category, interface, "
        "verification status, price, which rig leads it serves, and (introspected from the "
        "code, not hand-maintained) each signal's real unit and physical dimension. For "
        "choosing what to buy or wire up before a driver exists, not for a running rig's own "
        "tags -- that's `list_drivers`. Runs `<linux_dir>/scripts/search_drivers.py` in that "
        "checkout's own environment on the machine this server runs on, so it is a drive-tier "
        "tool like `check_driver`: it executes what it finds there.",
        _object(
            {
                "linux_dir": _str("The `linux/` checkout's path, where this server runs."),
                "tag": _str("Substring match on the driver tag."),
                "category": _str("Exact match, e.g. humidity, gas, liquid, weight, actuator."),
                "interface": _str("Exact match, e.g. i2c_bespoke, uart, analog_adc, gpio."),
                "tier": _str("config_only, generic_link, or bespoke_driver."),
                "status": _str("done, in_progress, or planned."),
                "manufacturer": _str("Substring match."),
                "domain": _str("Which rig lead this serves, e.g. mushroom, aging, dosing_skids."),
                "unit": _str("A signal's unit symbol, e.g. ppm, °C, %RH."),
                "dimension": _str("A signal's physical dimension, e.g. Temperature, Fraction."),
                "text": _str("Substring match over the whole entry."),
            },
            "linux_dir",
        ),
        Tier.DRIVE,
        _search_drivers,
    ),
    Tool(
        "list_drivers",
        "Every tag the runner can build -- drivers and links -- with its config schema, "
        "description and the module it came from.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.get("/api/drivers"),
        route=("get", "/api/drivers"),
    ),
    Tool(
        "reload_drivers",
        "Import (again) every module in the runner's drivers directory, so a new or edited "
        "driver's tag can be attached; devices already built keep their old class. Runs "
        "those files' top level. Returns what each file registered and any import error.",
        _object(),
        Tier.DRIVE,
        lambda rig, a: rig.post("/api/drivers/reload"),
        route=("post", "/api/drivers/reload"),
        changes_tools=True,
    ),
    Tool(
        "probe_hardware",
        "What the runner's host has: board model, I2C/SPI/serial buses, GPIO chips; with "
        "`scan`, the addresses answering on each I2C bus (a bus transaction: some devices "
        "mind).",
        _object({"scan": _bool("Scan the I2C buses.")}),
        Tier.READ,
        lambda rig, a: rig.get("/api/probe" + _query(scan=str(bool(a.get("scan"))).lower())),
        route=("get", "/api/probe"),
    ),
    Tool(
        "link_query",
        "One raw exchange on a link the runner owns: send `text`, return the reply (`*IDN?` "
        "to find out what is there; a guessed command to see if it works). Nothing is parsed.",
        _object(
            {"link": _str("The link's name in the rig file."), "text": _str("What to send.")},
            "link",
            "text",
        ),
        Tier.DRIVE,
        lambda rig, a: rig.post(f"/api/links/{a['link']}/query", {"text": a["text"]}),
        route=("post", "/api/links/{name}/query"),
    ),
    Tool(
        "attach_device",
        "Build a device from a rig-file entry and add it to the running rig, its links "
        "resolved; `check_rig` the whole file first. `save_rig` keeps it. On a hardware rig "
        "only when the runner runs with `--compose`.",
        _object(
            {
                "name": NAME,
                "entry": {
                    "type": "object",
                    "description": "The device entry: `driver`, optional `label`, `poll_s`, "
                    "`signals`, `bound`, and the driver's own settings flat or under `config`.",
                },
            },
            "name",
            "entry",
        ),
        Tier.DRIVE,
        lambda rig, a: rig.post("/api/devices", {"name": a["name"], **a["entry"]}),
        route=("post", "/api/devices"),
        changes_tools=True,
    ),
    Tool(
        "detach_device",
        "Stop and remove a device from the running rig; its controllers go with it.",
        _object({"name": NAME}, "name"),
        Tier.DRIVE,
        lambda rig, a: rig.delete(f"/api/devices/{a['name']}"),
        route=("delete", "/api/devices/{name}"),
        destructive=True,
        changes_tools=True,
    ),
    Tool(
        "attach_link",
        "Build a transport on the running rig and hold it under `name`, for devices to be "
        "built on: the rig file's `links:` entry (`tag`, its settings). On a hardware rig only "
        "when the runner runs with `--compose`.",
        _object(
            {"name": NAME, "config": {"type": "object", "description": "The link config."}},
            "name",
            "config",
        ),
        Tier.DRIVE,
        lambda rig, a: rig.post("/api/links", {"name": a["name"], **a["config"]}),
        route=("post", "/api/links"),
    ),
    Tool(
        "detach_link",
        "Drop a link no device is built on.",
        _object({"name": NAME}, "name"),
        Tier.DRIVE,
        lambda rig, a: rig.delete(f"/api/links/{a['name']}"),
        route=("delete", "/api/links/{name}"),
        destructive=True,
    ),
    Tool(
        "attach_document",
        "Add a whole rig document -- `links`, `devices`, `controllers` -- to the running rig, "
        "in that order; the way to build a rig from nothing. Validated whole before anything "
        "is built; a failure part-way leaves what was built before it. `check_rig` first. On a "
        "hardware rig only when the runner runs with `--compose`.",
        _object({"document": DOCUMENT}, "document"),
        Tier.DRIVE,
        lambda rig, a: rig.post("/api/rig", a["document"]),
        route=("post", "/api/rig"),
        changes_tools=True,
    ),
    Tool(
        "rig_document",
        "The running rig as a rig file would build it: links, devices and controllers as they "
        "are now, attached ones included.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.get("/api/rig/document"),
        route=("get", "/api/rig/document"),
    ),
    Tool(
        "rig_changes",
        "What differs between the running rig and the files it was loaded from, as an overlay: "
        "added or changed keys with their values, removed ones as null.",
        _object(),
        Tier.READ,
        lambda rig, a: rig.get("/api/rig/changes"),
        route=("get", "/api/rig/changes"),
    ),
    Tool(
        "rig_versions",
        "Every change made to the running rig through the API, newest first, with its reason; "
        "`rig_version` for one's document, `restore_rig_version` to go back.",
        _object({"limit": _int("At most this many.", minimum=1)}),
        Tier.READ,
        lambda rig, a: rig.get("/api/rig/versions" + _query(limit=a.get("limit"))),
        route=("get", "/api/rig/versions"),
    ),
    Tool(
        "rig_version",
        "One recorded rig version, with its whole document.",
        _object({"version_id": _int("From `rig_versions`.")}, "version_id"),
        Tier.READ,
        lambda rig, a: rig.get(f"/api/rig/versions/{a['version_id']}"),
        route=("get", "/api/rig/versions/{version_id}"),
    ),
    Tool(
        "restore_rig_version",
        "Make the running rig match a recorded version: what is absent is removed, what is "
        "missing is added, a changed device is rebuilt, controllers re-attached.",
        _object({"version_id": _int("From `rig_versions`.")}, "version_id"),
        Tier.DRIVE,
        lambda rig, a: rig.post(f"/api/rig/versions/{a['version_id']}/restore"),
        route=("post", "/api/rig/versions/{version_id}/restore"),
        destructive=True,
        changes_tools=True,
    ),
    Tool(
        "save_rig",
        "Write the running rig out. No `path`: what changed since the files were loaded, to "
        "an overlay beside the rig file the runner loads next start. A `path`: the whole rig "
        "to that file (`overwrite` to flatten onto one it was loaded from).",
        _object({
            "path": _str("Where to write; a .yaml, .toml or .json."),
            "overwrite": _bool("Allow `path` to be a file the rig was loaded from."),
        }),
        Tier.AUTHOR,
        lambda rig, a: rig.post("/api/rig/save", a),
        route=("post", "/api/rig/save"),
    ),
)


def _served(rig: Rig) -> set[tuple[str, str]]:
    """(method, path) the runner serves, from its OpenAPI; empty if it has none."""
    try:
        paths = rig.get("/openapi.json")["paths"]
    except RigError:  # a runner without OpenAPI lists no gated tool
        return set()
    return {(method, path) for path, methods in paths.items() for method in methods}


# endregion


def tools_for(rig: Rig, mode: str) -> list[Tool]:
    """Every tool the mode allows, fixed ones first, then the rig's own commands."""
    tier = MODES[mode]
    served = _served(rig)
    tools = [
        t
        for t in (*READ, *AUTHOR, *DRIVE, *DRIVERS)
        if t.tier <= tier and (t.route is None or t.route in served)
    ]
    if tier >= Tier.DRIVE:
        simulated = bool(rig.sim().get("simulated"))
        tools.extend(_device_tools(rig, simulated))
        if simulated:
            tools.extend(SIM)
    return tools
