"""Reading a document from disk, whatever it is written in.

Every file the rig reads is a tree of mappings and lists; the format is the
author's choice by suffix (`.toml`, `.yaml`, `.json`). All three load to the
same plain data, and meaning is decided after, by the model that validates it.

"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

SUFFIXES = (".toml", ".yaml", ".yml", ".json")


def loads(text: str, suffix: str) -> Any:
    """Parse `text` as the format `suffix` names."""
    match suffix.lower():
        case ".toml":
            return tomllib.loads(text)
        case ".yaml" | ".yml":
            import yaml  # the one non-stdlib parser, only when a YAML file is read

            return yaml.safe_load(text)
        case ".json":
            return json.loads(text)
    raise ValueError(f"unknown document format {suffix!r}; use one of {', '.join(SUFFIXES)}")


def load_document(path: str | Path) -> Any:
    """The plain data in `path`, by its suffix."""
    path = Path(path)
    if path.suffix.lower() not in SUFFIXES:  # say so before touching the file
        raise ValueError(f"{path}: unknown format; use one of {', '.join(SUFFIXES)}")
    return loads(path.read_text(), path.suffix)
