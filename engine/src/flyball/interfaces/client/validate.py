"""Enough JSON Schema to check a command's arguments before sending them.

Covers what the server's argument schemas use: objects, required, scalar
types, enum and `oneOf` of constants, bounds, `anyOf`, `$ref` into `$defs`,
null. Errors say where and why in the schema's terms.
"""

from __future__ import annotations

from typing import Any


class SchemaError(ValueError):
    """The arguments do not fit the command's schema."""


def validate(
    schema: dict[str, Any], value: Any, where: str = "", defs: dict[str, Any] | None = None
) -> None:
    """Raise [SchemaError][flyball.client.validate.SchemaError] if `value` fails `schema`."""
    defs = {**(defs or {}), **schema.get("$defs", {})}
    _check(schema, value, where or "value", defs)


def _resolve(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        try:
            return _resolve(defs[name], defs)
        except KeyError:
            raise SchemaError(f"schema refers to unknown definition {name!r}") from None
    return schema


def _check(schema: dict[str, Any], value: Any, where: str, defs: dict[str, Any]) -> None:
    schema = _resolve(schema, defs)

    if "anyOf" in schema or "oneOf" in schema:
        options = schema.get("anyOf") or schema["oneOf"]
        if "const" in _resolve(options[0], defs):  # a labelled enum
            allowed = [_resolve(o, defs)["const"] for o in options]
            if value not in allowed:
                raise SchemaError(f"{where}: {value!r} is not one of {allowed}")
            return
        errors = []
        for option in options:
            try:
                _check(option, value, where, defs)
                return
            except SchemaError as e:
                errors.append(str(e))
        raise SchemaError(
            f"{where}: {value!r} fits none of the alternatives:\n  " + "\n  ".join(errors)
        )

    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"{where}: {value!r} is not one of {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        raise SchemaError(f"{where}: must be {schema['const']!r}")

    kind = schema.get("type")
    if kind == "null":
        if value is not None:
            raise SchemaError(f"{where}: must be null")
        return
    if kind is None and value is None and "properties" not in schema:
        return
    if kind == "object" or "properties" in schema:
        _check_object(schema, value, where, defs)
    elif kind == "array":
        if not isinstance(value, (list, tuple)):
            raise SchemaError(f"{where}: expected a list, got {type(value).__name__}")
        _bounds(schema, len(value), where, "minItems", "maxItems", "items")
        items = schema.get("items")
        prefix = schema.get("prefixItems", [])
        for i, item in enumerate(value):
            sub = prefix[i] if i < len(prefix) else items
            if sub is not None:
                _check(sub, item, f"{where}[{i}]", defs)
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SchemaError(f"{where}: expected a number, got {type(value).__name__}")
        _numeric_bounds(schema, value, where)
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise SchemaError(f"{where}: expected an integer, got {type(value).__name__}")
        _numeric_bounds(schema, value, where)
    elif kind == "boolean" and not isinstance(value, bool):
        raise SchemaError(f"{where}: expected true or false, got {value!r}")
    elif kind == "string" and not isinstance(value, str):
        raise SchemaError(f"{where}: expected a string, got {type(value).__name__}")


def _check_object(schema: dict[str, Any], value: Any, where: str, defs: dict[str, Any]) -> None:
    if not isinstance(value, dict):
        raise SchemaError(f"{where}: expected a mapping, got {type(value).__name__}")
    properties = schema.get("properties", {})
    missing = [k for k in schema.get("required", []) if k not in value]
    if missing:
        raise SchemaError(f"{where}: missing {missing}")
    if schema.get("additionalProperties") is False:
        unknown = [k for k in value if k not in properties]
        if unknown:
            raise SchemaError(f"{where}: unknown {unknown}; expected {sorted(properties)}")
    for key, item in value.items():
        if key in properties:
            _check(properties[key], item, f"{where}.{key}", defs)


def _numeric_bounds(schema: dict[str, Any], value: float, where: str) -> None:
    if (lo := schema.get("minimum")) is not None and value < lo:
        raise SchemaError(f"{where}: {value} is below the minimum {lo}")
    if (lo := schema.get("exclusiveMinimum")) is not None and value <= lo:
        raise SchemaError(f"{where}: {value} must be greater than {lo}")
    if (hi := schema.get("maximum")) is not None and value > hi:
        raise SchemaError(f"{where}: {value} is above the maximum {hi}")
    if (hi := schema.get("exclusiveMaximum")) is not None and value >= hi:
        raise SchemaError(f"{where}: {value} must be less than {hi}")


def _bounds(
    schema: dict[str, Any], n: int, where: str, lo_key: str, hi_key: str, noun: str
) -> None:
    if (lo := schema.get(lo_key)) is not None and n < lo:
        raise SchemaError(f"{where}: needs at least {lo} {noun}, got {n}")
    if (hi := schema.get(hi_key)) is not None and n > hi:
        raise SchemaError(f"{where}: at most {hi} {noun}, got {n}")
