"""Tagged configs and their discriminated unions."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from flyball.core.config import Config, resolve


@pytest.fixture
def kinds(fresh):
    a, b = fresh("link_a"), fresh("link_b")

    class A(Config[str], tag=a):
        x: int = 1

        def build(self) -> str:
            return f"A{self.x}"

    class B(Config[str], tag=b):
        y: str

        def build(self) -> str:
            return f"B{self.y}"

    return a, b, A, B


def test_union_picks_the_config_by_tag_and_builds_it(kinds):
    a, b, A, B = kinds
    adapter = TypeAdapter(Config.union(A, B))
    assert adapter.validate_python({"tag": a, "x": 2}).build() == "A2"
    assert adapter.validate_python({"tag": b, "y": "hi"}).build() == "Bhi"
    with pytest.raises(ValidationError):
        adapter.validate_python({"x": 2})  # no tag: no guess
    with pytest.raises(ValidationError):
        adapter.validate_python({"tag": "nope"})


def test_schema_declares_the_discriminator(kinds):
    a, b, A, B = kinds
    schema = TypeAdapter(Config.union(A, B)).json_schema()
    assert schema["discriminator"]["propertyName"] == "tag"
    assert {m.get("$ref", "").rsplit("/", 1)[-1] for m in schema["oneOf"]} == {"ATagged", "BTagged"}


def test_code_never_passes_the_tag(kinds):
    a, b, A, B = kinds
    assert A().build() == "A1" and A.config_tag == a
    assert resolve(A(x=3)) == "A3" and resolve("already built") == "already built"


def test_duplicate_tag_is_refused(kinds):
    a, *_ = kinds
    with pytest.raises(ValueError, match="already"):

        class Again(Config[str], tag=a):
            def build(self) -> str:
                return ""


def test_untagged_config_cannot_join_a_union():
    class Plain(Config[str]):
        def build(self) -> str:
            return ""

    with pytest.raises(TypeError, match="has no tag"):
        Plain.tagged()
