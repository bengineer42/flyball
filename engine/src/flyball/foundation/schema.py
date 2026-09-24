"""JSON Schema as flyball generates it: a field with no declared title is titled by `humanise`.

Pydantic titles an untitled field `poll_s` "Poll S" (title case). A form, the rig file's
schema in an editor and an MCP tool's arguments show that title, so it follows the one rule
every label does (D-086): `poll_s` -> "Poll s", `dry_pump_flow` -> "Dry pump flow". A field
that declares `Field(title="Poll period")` keeps its own.

Every schema flyball serves or writes passes `schema_generator=Titled`:
`Model.model_json_schema(schema_generator=Titled)`,
`TypeAdapter(t).json_schema(schema_generator=Titled)`.
"""

from __future__ import annotations

from pydantic.json_schema import GenerateJsonSchema

from .keys import humanise


class Titled(GenerateJsonSchema):
    """`GenerateJsonSchema` with an untitled field, argument or item titled `humanise(name)`."""

    def get_title_from_name(self, name: str) -> str:
        return humanise(name)


__all__ = ["Titled"]
