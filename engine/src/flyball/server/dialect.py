"""The program file dialect: how a person writes steps, and its schema.

A program file is YAML. Each step is *externally tagged* -- the command's tag
is the key, its arguments the value:

    - ramp: {to: 60, pace: {seconds: 600}}
    - flag: "sample loaded"                  # scalar shorthand: the command's `primary` field
    - setpoint: 50
      settle: {within: 0.5, readings: 5}     # a modifier alongside the command

The HTTP API speaks the *internally tagged* form (`{"command": "ramp", ...}`).
This module bridges them: a normaliser rewrites a file step into that form
before validation, and a schema emitter describes the file form from the same
command registry, so the two cannot disagree.

Modifiers -- keys allowed beside the command -- are declared in a
[Dialect][flyball.server.dialect.Dialect] by the application.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_args, get_type_hints

import yaml
from pydantic import TypeAdapter

from flyball.core.clock import DURATION_KEYS, RATE_KEYS, Duration, Rate
from flyball.core.files import load_document
from flyball.programmer.command import Command, Commands
from flyball.programmer.program import Program
from flyball.server.commands import command_request, request_for


@dataclass(frozen=True, slots=True)
class Modifier:
    """A key allowed beside the command in a file step.

    `key` is what the author writes; `field` is where it lands in the
    normalised step; `schema` describes its value for the file schema.
    """

    key: str
    field: str
    schema: dict[str, Any]
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Dialect:
    """What a step may contain beyond its command."""

    modifiers: tuple[Modifier, ...] = ()
    commands: Mapping[str, type[Command]] = field(default_factory=lambda: Commands)

    @property
    def modifier_keys(self) -> dict[str, Modifier]:
        return {m.key: m for m in self.modifiers}


class StepError(ValueError):
    """A file step that the dialect cannot read."""


def _time_keys(annotation: Any) -> dict[str, Any] | None:
    """The flat keys a `Duration`/`Rate` field may be spelt with, or None if it is neither."""
    members = get_args(annotation) or (annotation,)
    keys: dict[str, Any] = {}
    for member in members:
        if isinstance(member, type) and issubclass(member, Duration):
            keys.update({key: {"type": "number", "minimum": 0} for key in DURATION_KEYS})
        elif isinstance(member, type) and issubclass(member, Rate):
            keys.update({key: {"type": "number"} for key in RATE_KEYS})
    return keys or None


_FOLDS: dict[type[Command], tuple[str, dict[str, Any]] | None] = {}


def foldable(command: type[Command]) -> tuple[str, dict[str, Any]] | None:
    """The one field of `command` that may be written flat, with its keys.

    `ramp: {to: 60, per_minute: 2}` stands for `ramp: {to: 60, pace: {per_minute: 2}}`.
    Only when exactly one field is a duration or rate; otherwise flat keys
    would be ambiguous.
    """
    if command not in _FOLDS:
        candidates = [
            (name, keys)
            for name, annotation in get_type_hints(command).items()
            if name != "tag" and (keys := _time_keys(annotation)) is not None
        ]
        _FOLDS[command] = candidates[0] if len(candidates) == 1 else None
    return _FOLDS[command]


def _unfold(command: type[Command], arguments: dict[str, Any], where: str) -> dict[str, Any]:
    """Gather flat time keys into the field they stand for."""
    fold = foldable(command)
    if fold is None:
        return arguments
    name, keys = fold
    flat = {key: arguments.pop(key) for key in list(arguments) if key in keys}
    if not flat:
        return arguments
    if name in arguments:
        raise StepError(f"{where}: give {name!r} or {sorted(flat)}, not both")
    return {**arguments, name: flat}


def normalise_step(raw: Any, dialect: Dialect, index: int | None = None) -> dict[str, Any]:
    """One file step -> `{"command": {...internally tagged...}, <modifier field>: ...}`.

    A step is a mapping with one command key plus any modifier keys; any other
    key is an error. A scalar or list under the command key means its
    `primary` field; a mapping is the arguments verbatim.
    """
    where = f"step {index}" if index is not None else "step"
    if not isinstance(raw, Mapping):
        raise StepError(f"{where}: expected a mapping, got {type(raw).__name__}")
    modifiers = dialect.modifier_keys
    tags = [key for key in raw if key in dialect.commands]
    unknown = [key for key in raw if key not in dialect.commands and key not in modifiers]
    if unknown:
        raise StepError(
            f"{where}: unknown key(s) {unknown}; commands are {sorted(dialect.commands)}"
        )
    if len(tags) != 1:
        raise StepError(f"{where}: a step names exactly one command, found {tags or 'none'}")
    tag = tags[0]
    command = dialect.commands[tag]
    body = raw[tag]
    if isinstance(body, Mapping):
        arguments = dict(body)
    elif body is None:
        arguments = {}
    elif command.primary is not None:
        arguments = {command.primary: body}
    else:
        raise StepError(f"{where}: {tag!r} takes a mapping of arguments, not {body!r}")
    if "command" in arguments:
        raise StepError(f"{where}: 'command' is not an argument of {tag!r}")
    arguments = _unfold(command, arguments, where)
    step: dict[str, Any] = {"command": {"command": tag, **arguments}}
    for key in raw:
        if key != tag:
            step[modifiers[key].field] = raw[key]
    return step


def normalise_program(document: Any, dialect: Dialect) -> dict[str, Any]:
    """A loaded program file -> the internally tagged document."""
    if not isinstance(document, Mapping) or not isinstance(document.get("steps"), list):
        raise StepError("a program is a mapping with a 'steps' list")
    steps = [normalise_step(step, dialect, i) for i, step in enumerate(document["steps"])]
    return {**{k: v for k, v in document.items() if k != "steps"}, "steps": steps}


def program_from_file(path: str | Path, dialect: Dialect) -> Program:
    """A program from `.yaml`, `.toml` or `.json`; the dialect is the same in each."""
    return program_from_document(load_document(path), dialect)


def program_from_document(document: Any, dialect: Dialect) -> Program:
    """A loaded program document -> a [Program][flyball.programmer.program.Program] of commands."""
    normalised = normalise_program(document, dialect)
    adapter = TypeAdapter(command_request())
    commands = [adapter.validate_python(step["command"]).parse() for step in normalised["steps"]]
    return Program(commands, name=normalised.get("name"), description=normalised.get("description"))


def commands_from_yaml(text: str, dialect: Dialect) -> Program:
    """Parse a program file into a [Program][flyball.programmer.program.Program] of commands.

    Modifiers are validated but not attached: wrapping a command in a
    completion or duration is the programmer's job.
    """
    return program_from_document(yaml.safe_load(text), dialect)


def step_schema(dialect: Dialect) -> dict[str, Any]:
    """JSON schema for one file step, from the command registry.

    One `oneOf` branch per command, requiring its key. The value is the
    request schema without `command`, or the bare `primary` field's schema as
    an alternative. Modifier keys are allowed on every branch.
    """
    modifiers = {
        m.key: {**m.schema, **({"description": m.description} if m.description else {})}
        for m in dialect.modifiers
    }
    defs: dict[str, Any] = {}
    branches: list[dict[str, Any]] = []
    for tag, command in dialect.commands.items():
        request = TypeAdapter(request_for(command)).json_schema(ref_template="#/$defs/{model}")
        defs.update(request.pop("$defs", {}))
        request["properties"].pop("command", None)
        request["required"] = [r for r in request.get("required", []) if r != "command"] or None
        if request["required"] is None:
            del request["required"]
        request["additionalProperties"] = False
        if (fold := foldable(command)) is not None:
            # The flat spelling: the folded field's keys beside the other
            # arguments, and the field itself no longer required.
            name, keys = fold
            request["properties"].update(keys)
            request["required"] = [r for r in request.get("required", []) if r != name] or None
            if request["required"] is None:
                del request["required"]
        value: dict[str, Any] = request
        if command.primary is not None and command.primary in request["properties"]:
            value = {"anyOf": [request["properties"][command.primary], request]}
        branches.append({
            "type": "object",
            "properties": {tag: value, **modifiers},
            "required": [tag],
            "additionalProperties": False,
            **({"description": command.__doc__.strip()} if command.__doc__ else {}),
        })
    return {"oneOf": branches, **({"$defs": defs} if defs else {})}


def program_schema(dialect: Dialect, title: str = "program") -> dict[str, Any]:
    """JSON schema for a whole program file."""
    step = step_schema(dialect)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": title,
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "What the program is called; the file's stem when absent.",
            },
            "description": {
                "type": "string",
                "description": "What the program is for, in a sentence or a paragraph; optional.",
            },
            "steps": {"type": "array", "minItems": 1, "items": {"oneOf": step["oneOf"]}},
        },
        "required": ["steps"],
        **({"$defs": step["$defs"]} if "$defs" in step else {}),
    }
