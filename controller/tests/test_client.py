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
            validate(FLOW, body, where="set_fraction")


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


class TestProgramCommands:
    """`flyball program run/status/stop` talk to the programs routes and print the state."""

    def test_run_posts_the_document_and_prints_the_state(self, tmp_path, capsys):
        import argparse

        from flyball import cli

        path = tmp_path / "p.yaml"
        path.write_text("name: t\nsteps:\n  - wait: press go\n")
        calls = []

        class Fake:
            def post(self, route, body=None):
                calls.append((route, body))
                return {"running": True, "step": 0, "steps": 1, "command": "wait"}

            def get(self, route):
                calls.append((route, None))
                return {"running": False, "step": 0, "steps": 0, "command": None}

        cli.cmd_program_run(Fake(), argparse.Namespace(json=False, path=path, interrupt=True))
        cli.cmd_program_status(Fake(), argparse.Namespace(json=False))
        cli.cmd_program_stop(Fake(), argparse.Namespace(json=False))
        assert calls[0] == (
            "/api/programs/run?interrupt=true",
            {"name": "t", "steps": [{"wait": "press go"}]},
        )
        assert calls[1] == ("/api/programs/running", None)
        assert calls[2] == ("/api/programs/interrupt", None)
        out = capsys.readouterr().out.splitlines()
        assert out == ["running: step 1 of 1 (wait)", "idle", "running: step 1 of 1 (wait)"]


def _rig_toml(reader_name: str, actuator_name: str) -> str:
    return f"""
[links.bench]
tag = "fake_text"
replies = {{}}

[[readers]]
[readers.device]
tag = "scpi_reader"
name = "{reader_name}"
link = "bench"
measurands = {{}}

[[actuators]]
tag = "scpi_actuator"
name = "{actuator_name}"
link = "bench"
command = "SOUR:VOLT {{value}}"
"""


class TestRigCheck:
    """`flyball rig check` validates a file with no rig, and reports a name collision."""

    def test_reports_a_name_collision(self, tmp_path):
        from flyball import cli
        from flyball.client import SchemaError

        path = tmp_path / "rig.toml"
        path.write_text(_rig_toml("x", "x"))
        with pytest.raises(SchemaError, match="already used by reader 'x'"):
            cli.cmd_rig_check(None, argparse.Namespace(path=path))

    def test_a_consistent_file_prints_a_summary(self, tmp_path, capsys):
        from flyball import cli

        path = tmp_path / "rig.toml"
        path.write_text(_rig_toml("x", "y"))
        cli.cmd_rig_check(None, argparse.Namespace(path=path))
        out = capsys.readouterr().out
        assert "ok" in out and "1 readers, 1 actuators" in out
