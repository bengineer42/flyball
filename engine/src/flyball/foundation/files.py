"""Reading a document from disk, whatever it is written in.

Every file the rig reads is a tree of mappings and lists; the format is the
author's choice by suffix (`.toml`, `.yaml`, `.json`). All three load to the
same plain data, and meaning is decided after, by the model that validates it.

A device and a controller table are keyed by name, so a duplicate key is a
mistake the author almost certainly did not intend -- and the tag registry
cannot even name which entry it meant. TOML already refuses one; the loaders
here are strict the same way for YAML and JSON, which otherwise silently keep
the last value and hide the bug.

"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Hashable
from pathlib import Path
from typing import Any

SUFFIXES = (".toml", ".yaml", ".yml", ".json")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """`object_pairs_hook` for `json.loads`: refuse a key seen twice in one object."""
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r} in JSON object")
        seen[key] = value
    return seen


_strict_yaml_loader: type[Any] | None = None


def _yaml_loader() -> type[Any]:
    """A `yaml.SafeLoader` that refuses a mapping with a repeated key, built once.

    PyYAML's own `construct_mapping` keeps the last value silently; this
    overrides it to check for a repeat first, naming the key and the line
    it reappears on, before deferring to the original for the real work.
    """
    global _strict_yaml_loader
    if _strict_yaml_loader is None:
        import yaml

        class StrictLoader(yaml.SafeLoader):
            def construct_mapping(
                self, node: yaml.MappingNode, deep: bool = False
            ) -> dict[Hashable, Any]:
                seen: set[Any] = set()
                for key_node, _ in node.value:
                    key = self.construct_object(key_node, deep=deep)
                    if key in seen:
                        raise ValueError(
                            f"duplicate key {key!r} at line {key_node.start_mark.line + 1}"
                        )
                    seen.add(key)
                return super().construct_mapping(node, deep=deep)

        _strict_yaml_loader = StrictLoader
    return _strict_yaml_loader


def loads(text: str, suffix: str) -> Any:
    """Parse `text` as the format `suffix` names.

    Raises:
        ValueError: The text is not valid in that format, including a
            mapping with a key repeated (TOML rejects this on its own;
            YAML and JSON are made to as well).
    """
    match suffix.lower():
        case ".toml":
            return tomllib.loads(text)
        case ".yaml" | ".yml":
            import yaml  # the one non-stdlib parser, only when a YAML file is read

            return yaml.load(text, Loader=_yaml_loader())
        case ".json":
            return json.loads(text, object_pairs_hook=_no_duplicate_keys)
    raise ValueError(f"unknown document format {suffix!r}; use one of {', '.join(SUFFIXES)}")


def load_document(path: str | Path) -> Any:
    """The plain data in `path`, by its suffix."""
    path = Path(path)
    if path.suffix.lower() not in SUFFIXES:  # say so before touching the file
        raise ValueError(f"{path}: unknown format; use one of {', '.join(SUFFIXES)}")
    return loads(path.read_text(), path.suffix)


def dumps(data: Any, suffix: str) -> str:
    """Serialise `data` as the format `suffix` names, in a canonical style.

    For `rig check --print`: block-style YAML, indented JSON, and TOML
    (via `tomli_w`) with keys in the order given -- never the flow style
    `yaml.dump`'s defaults would otherwise pick for a nested mapping.
    """
    match suffix.lower():
        case ".toml":
            import tomli_w

            return tomli_w.dumps(data)
        case ".yaml" | ".yml":
            import yaml

            return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        case ".json":
            return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    raise ValueError(f"unknown document format {suffix!r}; use one of {', '.join(SUFFIXES)}")


def dumps_without_none(data: Any, suffix: str) -> str:
    """Serialise `data` like [dumps][flyball.foundation.files.dumps], but drop `None` values first.

    TOML has no null; a document built by overlaying a change (an unset
    override coming out as `None`) onto a loaded file would otherwise fail
    to dump as `.toml`. Used for saving a rig's or a simulation's current
    state back to its file.
    """
    match suffix.lower():
        case ".toml":
            import tomli_w

            return tomli_w.dumps(_without_none(data))
        case ".yaml" | ".yml":
            import yaml

            return yaml.safe_dump(data, sort_keys=False)
        case ".json":
            return json.dumps(data, indent=2) + "\n"
    raise ValueError(f"unknown document format {suffix!r}; use one of {', '.join(SUFFIXES)}")


def _without_none(value: Any) -> Any:
    """Drop `None` values recursively, so a TOML dump does not need to represent null."""
    if isinstance(value, dict):
        return {k: _without_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_without_none(v) for v in value]
    return value
