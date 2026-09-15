"""The schema-driven client's validator and the CLI's schema-to-argparse mapping."""

from __future__ import annotations

import argparse

import pytest

from flyball.cli import add_arguments, body_from
from flyball.client import SchemaError, validate

FLOW = {
    "$defs": {
        "Absolute": {
            "type": "object",
            "title": "Absolute",
            "properties": {"flow": {"type": "number", "minimum": 0}, "tag": {"const": "absolute"}},
            "required": ["flow"],
        },
        "Relative": {
            "type": "object",
            "title": "Relative",
            "properties": {"fraction": {"type": "number", "minimum": 0, "maximum": 1}},
            "additionalProperties": False,
        },
    },
    "type": "object",
    "properties": {
        "wet_fraction": {"type": "number", "minimum": 0, "maximum": 1, "unit": "%"},
        "flow": {"anyOf": [{"$ref": "#/$defs/Absolute"}, {"$ref": "#/$defs/Relative"}]},
        "mode": {
            "oneOf": [{"const": "raise", "title": "Refuse"}, {"const": "clamp", "title": "Clamp"}],
            "default": "raise",
        },
        "on": {"type": "boolean"},
    },
    "required": ["wet_fraction", "flow"],
    "additionalProperties": False,
}


class TestValidate:
    def test_accepts_a_good_body(self):
        validate(FLOW, {"wet_fraction": 0.5, "flow": {"flow": 8}, "mode": "clamp", "on": True})

    @pytest.mark.parametrize(
        ("body", "message"),
        [
            ({"flow": {"flow": 8}}, "missing"),
            ({"wet_fraction": 2, "flow": {"flow": 8}}, "above the maximum"),
            ({"wet_fraction": 0.5, "flow": {"flow": 8}, "bogus": 1}, "unknown"),
            ({"wet_fraction": 0.5, "flow": {"flow": 8}, "mode": "explode"}, "not one of"),
            ({"wet_fraction": "x", "flow": {"flow": 8}}, "expected a number"),
            ({"wet_fraction": 0.5, "flow": {"flow": -1}}, "fits none"),
            ({"wet_fraction": 0.5, "flow": {"flow": 8}, "on": "yes"}, "true or false"),
        ],
    )
    def test_rejects_with_the_schema_s_words(self, body, message):
        with pytest.raises(SchemaError, match=message):
            validate(FLOW, body, where="set_blend")


class TestCli:
    def parser(self):
        p = argparse.ArgumentParser(prog="t")
        add_arguments(p, FLOW, FLOW["$defs"])
        return p

    def test_flags_from_schema(self):
        help_text = self.parser().format_help()
        assert "--wet-fraction" in help_text and "(%)" in help_text and ">= 0" in help_text
        assert (
            "--flow.flow" in help_text and "[absolute]" in help_text
        )  # the tag const labels the branch
        assert "--mode {raise,clamp}" in help_text and "Refuse" in help_text
        assert "--on" in help_text and "--no-on" in help_text

    def test_dotted_flags_nest_and_json_literals_parse(self):
        args = self.parser().parse_args([
            "--wet-fraction",
            "0.25",
            "--flow.flow",
            "8",
            "--mode",
            "clamp",
            "--no-on",
        ])
        assert body_from(args, frozenset()) == {
            "wet_fraction": 0.25,
            "flow": {"flow": 8},
            "mode": "clamp",
            "on": False,
        }
        args = self.parser().parse_args(["--wet-fraction", "0.25", "--flow", '{"fraction": 0.5}'])
        assert body_from(args, frozenset())["flow"] == {"fraction": 0.5}

    def test_required_flags_are_required(self):
        with pytest.raises(SystemExit):
            self.parser().parse_args(["--flow.flow", "8"])
