"""Program documents as text: YAML, TOML or JSON, and the tree they all become.

The internal form is the plain tree -- mappings, lists, scalars -- that the
dialect normalises. Every format parses to it and dumps from it, so a
program stored as YAML downloads as TOML by ``dump(parse(body, "yaml"),
"toml")``. What does not survive a conversion: comments (they live only in
the original text, which is why the store keeps it verbatim), and ``null``
into TOML, which has no such value -- those keys are dropped.
"""

from __future__ import annotations

import json
import re
import tomllib
from typing import Any, Literal, cast

import yaml

from flyball.core.errors import UnachievableError

Format = Literal["yaml", "toml", "json"]

FORMATS: tuple[Format, ...] = ("yaml", "toml", "json")

MEDIA_TYPES: dict[Format, str] = {
    "yaml": "application/yaml",
    "toml": "application/toml",
    "json": "application/json",
}

EXTENSIONS: dict[str, Format] = {".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".json": "json"}


class FormatError(UnachievableError):
    """The text is not a document in the format claimed."""


def detect(content_type: str | None = None, filename: str | None = None) -> Format | None:
    """The format a request means, from its media type or the file's extension."""
    if content_type:
        media = content_type.split(";")[0].strip().lower()
        for fmt in FORMATS:
            if media.split("/")[-1].removeprefix("x-") == fmt or media.endswith(f"+{fmt}"):
                return fmt
    if filename:
        dot = filename.rfind(".")
        if dot >= 0 and (fmt := EXTENSIONS.get(filename[dot:].lower())):
            return fmt
    return None


def parse(text: str, format: Format) -> Any:
    """Text in `format` -> the document tree. Raises FormatError when it does not parse."""
    try:
        if format == "yaml":
            return yaml.safe_load(text)
        if format == "toml":
            return tomllib.loads(text)
        return json.loads(text)
    except (yaml.YAMLError, tomllib.TOMLDecodeError, json.JSONDecodeError) as e:
        raise FormatError(f"not valid {format}: {e}") from e


def dump(document: Any, format: Format) -> str:
    """The document tree -> text in `format`, keys in the author's order."""
    if format == "yaml":
        return cast(str, yaml.safe_dump(document, sort_keys=False, allow_unicode=True))
    if format == "json":
        return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    if not isinstance(document, dict):
        raise FormatError("TOML needs a mapping at the top level")
    return _dump_toml(document)


def _dump_toml(document: dict[str, Any]) -> str:
    """A small TOML writer: enough for the dialect (tables, arrays of tables, scalars).

    Written here rather than pulling in ``tomli_w``: the documents are shallow
    and the rules are few. ``None`` values are dropped, since TOML cannot say
    them.
    """
    lines: list[str] = []

    def scalar(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, str):
            return json.dumps(v, ensure_ascii=False)
        if isinstance(v, list):
            return "[" + ", ".join(inline(x) for x in v) + "]"
        if isinstance(v, dict):
            items = ", ".join(f"{key(k)} = {inline(x)}" for k, x in v.items() if x is not None)
            return "{" + items + "}"
        raise FormatError(f"cannot write {type(v).__name__} as TOML")

    def inline(v: Any) -> str:
        return scalar(v)

    def key(k: str) -> str:
        return k if k.replace("-", "").replace("_", "").isalnum() else json.dumps(k)

    def table(prefix: list[str], mapping: dict[str, Any]) -> None:
        plain = {k: v for k, v in mapping.items() if v is not None and not _is_table(v)}
        tables = {k: v for k, v in mapping.items() if _is_table(v)}
        if prefix and (plain or not tables):
            lines.append(f"[{'.'.join(key(p) for p in prefix)}]")
        for k, v in plain.items():
            lines.append(f"{key(k)} = {scalar(v)}")
        if plain or not prefix:
            lines.append("")
        for k, v in tables.items():
            if isinstance(v, dict):
                table([*prefix, k], v)
            else:  # list of dicts -> array of tables
                for item in v:
                    lines.append(f"[[{'.'.join(key(p) for p in [*prefix, k])}]]")
                    plain_item = {
                        ik: iv for ik, iv in item.items() if iv is not None and not _is_table(iv)
                    }
                    for ik, iv in plain_item.items():
                        lines.append(f"{key(ik)} = {scalar(iv)}")
                    for ik, iv in item.items():
                        if _is_table(iv) and isinstance(iv, dict):
                            table([*prefix, k, ik], iv)
                    lines.append("")

    table([], document)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).rstrip("\n") + "\n"
    # Round-trip check: what we wrote must read back as what we were given (nulls aside).
    if _strip_none(tomllib.loads(text)) != _strip_none(document):
        raise FormatError("document has a shape this TOML writer cannot express")
    return text


def _is_table(v: Any) -> bool:
    if isinstance(v, dict):
        return True
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def _strip_none(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _strip_none(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [_strip_none(x) for x in v]
    return v
