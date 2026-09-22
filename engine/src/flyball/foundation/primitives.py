"""Generic, cross-cutting helpers with no home in a more specific subpackage."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Any, Self

from pydantic import GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema


class Labelled(StrEnum):
    """A string enum whose members carry a display label.

    Declare members as `NAME = "wire_value", "Display label"`; the label
    becomes the option's title in the JSON schema. Omitted, the value is used.
    """

    label: str

    def __new__(cls, value: str, label: str = "") -> Self:
        member = str.__new__(cls, value)
        member._value_ = value
        member.label = label or value
        return member

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        schema = handler(core)
        schema.pop("enum", None)
        schema["oneOf"] = [{"const": member.value, "title": member.label} for member in cls]
        return schema


class UnsetType(Enum):
    UNSET = "Unset"

    def __repr__(self) -> str:
        return "Unset"


Unset = UnsetType.UNSET


def validate_normalised(name: str, value: float) -> float:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0.0 and 1.0, got {value}")
    return value


def require[T](value: T | None, error: type[Exception], *args: Any, **kwargs: Any) -> T:
    if value is None:
        raise error(*args, **kwargs)
    return value


@dataclass(frozen=True, slots=True)
class WithWarning[T]:
    value: T
    warning: Exception | None = None

    @property
    def ok(self) -> bool:
        return self.warning is None

    def try_raise(self) -> None:
        if self.warning is not None:
            raise self.warning

    def __call__(self) -> T:
        return self.value


__all__ = [
    "Labelled",
    "Unset",
    "UnsetType",
    "WithWarning",
    "require",
    "validate_normalised",
]
