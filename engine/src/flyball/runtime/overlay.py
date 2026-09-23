"""Layering rig files: merge, `--set`, and `extends`.

A rig is an ordered list of files, later overlaying earlier -- the
docker-compose `-f` / kustomize / Hydra pattern. `merge` is the one rule
([merge][flyball.runtime.overlay.merge]); everything else here is built on
it: `--set KEY=VALUE` parses to a path and a value and applies as a
one-key overlay ([parse_set][flyball.runtime.overlay.parse_set],
[apply_set][flyball.runtime.overlay.apply_set]), and a file may name its own
bases with `extends`, resolved before it is merged with the rest
([resolve_layers][flyball.runtime.overlay.resolve_layers]).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from flyball.foundation.files import load_document, yaml_loader

__all__ = ["apply_set", "merge", "parse_set", "resolve_layers"]


def merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """`overlay` layered onto `base`: mappings deep-merge, everything else replaces.

    A key whose overlay value is `None` is removed from the result -- the
    only way to delete something an earlier layer set. Neither argument is
    mutated.
    """
    result = dict(base)
    for key, value in overlay.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def parse_set(expr: str) -> tuple[list[str], Any]:
    """`"devices.furnace.noise=0.3"` -> `(["devices", "furnace", "noise"], 0.3)`.

    The value is parsed as a YAML scalar, so `0.3` is a float, `true` a
    bool, `null` is `None` (meaning delete, once applied), `[1, 2]` a list,
    and a bare word a string.

    Raises:
        ValueError: `expr` has no `=`.
    """
    key, sep, raw = expr.partition("=")
    if not sep:
        raise ValueError(f"{expr!r}: expected KEY=VALUE")
    import yaml  # the one non-stdlib parser, only needed to type a --set value

    return key.split("."), yaml.load(raw, Loader=yaml_loader())


def apply_set(document: dict[str, Any], path: list[str], value: Any) -> dict[str, Any]:
    """`document` with `value` set at `path`, creating intermediate mappings as needed.

    `value` of `None` deletes the key at `path` instead; deleting a path
    that is not there is a no-op. Pure: `document` is not mutated.

    Raises:
        ValueError: `path` is empty.
    """
    if not path:
        raise ValueError("empty path")
    head, *rest = path
    if not rest:
        result = dict(document)
        if value is None:
            result.pop(head, None)
        else:
            result[head] = value
        return result
    child = document.get(head)
    if value is None and not isinstance(child, dict):
        return document  # nothing at this path to delete into: a true no-op, nothing created
    result = dict(document)
    result[head] = apply_set(child if isinstance(child, dict) else {}, rest, value)
    return result


def _load_layer(path: Path, stack: tuple[Path, ...]) -> tuple[dict[str, Any], list[Path]]:
    """`path`, with its own `extends` resolved and stripped, and the files that contributed.

    `extends` is applied *under* `path` -- a base, in the order the list
    names them, each recursively resolved the same way -- then `path`'s own
    keys are merged on top.
    """
    resolved = path.resolve()
    if resolved in stack:
        cycle = " -> ".join(str(p) for p in (*stack, resolved))
        raise ValueError(f"extends cycle: {cycle}")
    document = load_document(path)
    if not isinstance(document, dict):
        raise ValueError(f"{path}: a rig file is a mapping")
    extends = document.get("extends", [])
    if not isinstance(extends, list) or not all(isinstance(e, str) for e in extends):
        raise ValueError(f"{path}: extends must be a list of paths")
    base: dict[str, Any] = {}
    contributed: list[Path] = []
    for name in extends:
        base_doc, base_files = _load_layer(path.parent / name, (*stack, resolved))
        base = merge(base, base_doc)
        contributed.extend(f for f in base_files if f not in contributed)
    own = {k: v for k, v in document.items() if k != "extends"}
    contributed.append(path)
    return merge(base, own), contributed


def resolve_layers(
    paths: Sequence[str | Path], sets: Sequence[str] = ()
) -> tuple[dict[str, Any], list[Path]]:
    """Every file in `paths`, each with its own `extends` resolved, merged in order.

    Later files in `paths` overlay earlier ones -- and, since each file's
    `extends` is resolved before it is merged with the rest, a base named by
    `extends` always loses to whatever the command line itself lists. Every
    `--set` in `sets` is applied last, in order.

    Returns:
        The merged document (`extends` stripped throughout) and every file
        that contributed, in the order it was first read.

    Raises:
        ValueError: A file's `extends` forms a cycle.
    """
    document: dict[str, Any] = {}
    contributed: list[Path] = []
    for path in paths:
        layer, files = _load_layer(Path(path), ())
        document = merge(document, layer)
        contributed.extend(f for f in files if f not in contributed)
    for expr in sets:
        set_path, value = parse_set(expr)
        document = apply_set(document, set_path, value)
    return document, contributed
