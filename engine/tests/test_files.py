"""Strict loading (a repeated key is refused, not silently overwritten) and canonical dumping."""

from __future__ import annotations

import pytest

from flyball.foundation.files import dumps, loads


def test_duplicate_yaml_key_names_the_key_and_the_line():
    with pytest.raises(ValueError, match="'a'") as info:
        loads("a: 1\nb: 2\na: 3\n", ".yaml")
    assert "line 3" in str(info.value)


def test_duplicate_yaml_key_is_caught_when_nested():
    with pytest.raises(ValueError, match="'x'"):
        loads("outer:\n  x: 1\n  y: 2\n  x: 3\n", ".yaml")


def test_a_valid_yaml_file_still_loads():
    assert loads("a: 1\nb: {c: 2, d: 3}\n", ".yaml") == {"a": 1, "b": {"c": 2, "d": 3}}


def test_duplicate_json_key_names_the_key():
    with pytest.raises(ValueError, match="'a'"):
        loads('{"a": 1, "b": 2, "a": 3}', ".json")


def test_duplicate_json_key_is_caught_when_nested():
    with pytest.raises(ValueError, match="'x'"):
        loads('{"outer": {"x": 1, "x": 2}}', ".json")


def test_a_valid_json_file_still_loads():
    assert loads('{"a": 1, "b": {"c": 2}}', ".json") == {"a": 1, "b": {"c": 2}}


def test_duplicate_toml_key_is_refused_too():
    """TOML already refuses this on its own; only checking it stays a `ValueError`."""
    with pytest.raises(ValueError):
        loads("a = 1\na = 2\n", ".toml")


def test_a_valid_toml_file_still_loads():
    assert loads("a = 1\n[b]\nc = 2\n", ".toml") == {"a": 1, "b": {"c": 2}}


def test_dumps_yaml_is_block_style_in_the_given_key_order():
    text = dumps({"z": 1, "a": {"y": 2, "x": 3}}, ".yaml")
    assert text == "z: 1\na:\n  y: 2\n  x: 3\n"


def test_dumps_yaml_keeps_unicode_readable():
    assert "°C" in dumps({"unit": "°C"}, ".yaml")


def test_dumps_json_is_indented():
    assert dumps({"a": 1}, ".json") == '{\n  "a": 1\n}\n'


def test_dumps_toml_round_trips_through_loads():
    document = {"a": 1, "b": {"c": 2, "d": [1, 2]}}
    assert loads(dumps(document, ".toml"), ".toml") == document


def test_dumps_unknown_suffix_raises():
    with pytest.raises(ValueError, match="unknown"):
        dumps({}, ".ini")
