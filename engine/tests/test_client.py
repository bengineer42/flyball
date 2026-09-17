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


class TestClientSurfaces:
    """`Rig.demand`/`.read`/`.controllers` build the routes the plan documents."""

    def test_demand_read_and_controllers_build_the_documented_routes(self):
        from flyball.client import Rig

        calls = []
        rig = Rig("http://x")
        rig.put = lambda path, body=None: calls.append(("PUT", path, body))
        rig.get = lambda path: calls.append(("GET", path, None)) or {"ok": True}

        rig.demand("heaters.heater1", 1200.0)
        rig.read("furnace.zone1")
        rig.read("furnace.zone1", fresh=True)
        rig.controllers()
        assert calls == [
            ("PUT", "/api/signals/heaters.heater1", 1200.0),
            ("GET", "/api/read/furnace.zone1", None),
            ("GET", "/api/read/furnace.zone1?fresh=true", None),
            ("GET", "/api/controllers", None),
        ]

    def test_devices_surface_replaces_actuators_and_readers(self):
        from flyball.client import Rig

        rig = Rig("http://x", schema={"devices": {"furnace": {"type": "SimDaq", "commands": {}}}})
        assert rig.devices.names() == ["furnace"]
        assert rig.devices["furnace"].name == "furnace"
        with pytest.raises(SchemaError, match="no device 'nowhere'"):
            rig.devices["nowhere"]


class TestDemandReadCommands:
    """`flyball demand`/`read` delegate to the client and print what it returns."""

    def test_cli_demand_and_read_delegate_to_the_client(self, capsys):
        import argparse

        from flyball import cli

        calls = []

        class Fake:
            def demand(self, address, value):
                calls.append(("demand", address, value))
                return {"value": value}

            def read(self, address, fresh=False):
                calls.append(("read", address, fresh))
                return {"reading": {"value": 42.0}}

        cli.cmd_demand(
            Fake(), argparse.Namespace(json=True, address="heaters.heater1", value=1200.0)
        )
        cli.cmd_read(Fake(), argparse.Namespace(json=True, address="furnace.zone1", fresh=True))
        assert calls == [("demand", "heaters.heater1", 1200.0), ("read", "furnace.zone1", True)]
        out = capsys.readouterr().out.splitlines()
        assert out == ['{"value": 1200.0}', '{"reading": {"value": 42.0}}']


class TestStatus:
    """`flyball status` renders devices (signals with latest/write), controllers and waits."""

    def test_renders_devices_controllers_and_waits(self, capsys):
        import argparse

        from flyball import cli

        class Fake:
            _routes = {
                "/api/health": {"ok": True, "uptime_s": 12.0, "recording": False},
                "/api/devices": [
                    {
                        "name": "furnace",
                        "driver": "sim_daq",
                        "conditions": [],
                        "signals": [
                            {
                                "name": "zone1",
                                "address": "furnace.zone1",
                                "access": "rp",
                                "latest": {"time_ns": 1, "value": 654.7},
                            },
                            {
                                "name": "dry",
                                "address": "furnace.dry",
                                "atomic": True,
                                "signals": [
                                    {
                                        "name": "humidity",
                                        "address": "furnace.dry.humidity",
                                        "access": "rp",
                                        "latest": {"time_ns": 1, "value": 4.1},
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "name": "heaters",
                        "driver": "sim_drive",
                        "conditions": [{"kind": "offline"}],
                        "signals": [
                            {
                                "name": "heater1",
                                "address": "heaters.heater1",
                                "access": "w",
                                "write": {"value": 1200.0, "requested": None, "at_limit": None},
                            },
                        ],
                    },
                ],
                "/api/controllers": [
                    {
                        "name": "heaters.heater1",
                        "mode": "regulating",
                        "reading": {"value": 654.7},
                        "setpoint": 700.0,
                        "law": {"tag": "PI"},
                    }
                ],
            }

            def get(self, route):
                return self._routes[route]

            def waits(self):
                return {"go": {"outcome": "pending", "message": "press go"}}

        cli.cmd_status(Fake(), argparse.Namespace(json=False))
        out = capsys.readouterr().out
        assert "OK  up 12 s  recording=no" in out
        assert "device" in out and "furnace" in out and "sim_daq" in out
        assert "device" in out and "heaters" in out and "offline" in out
        assert "furnace.zone1" in out and "[ rp]" in out and "654.7" in out
        assert "furnace.dry.humidity" in out and "4.1" in out
        assert "heaters.heater1" in out and "[  w]" in out and "1200.0" in out
        assert "controller" in out and "regulating" in out and "law=PI" in out
        assert "waiting" in out and "go" in out and "press go" in out


def _rig_toml(device_name: str) -> str:
    return f"""
[links.bench]
tag = "fake_text"
replies = {{}}

[devices.{device_name}]
driver = "scpi"
link = "bench"
channels = {{ voltage = {{ query = "MEAS:VOLT?", unit = "V" }} }}
"""


class TestRigCheck:
    """`flyball rig check` validates a file with no rig, and reports a reserved name."""

    def test_reports_a_reserved_name(self, tmp_path):
        from flyball import cli
        from flyball.client import SchemaError

        path = tmp_path / "rig.toml"
        path.write_text(_rig_toml("schema"))
        with pytest.raises(SchemaError, match="'schema' is reserved"):
            cli.cmd_rig_check(None, argparse.Namespace(paths=[path], sets=[], print=False))

    def test_a_consistent_file_prints_a_summary(self, tmp_path, capsys):
        from flyball import cli

        path = tmp_path / "rig.toml"
        path.write_text(_rig_toml("x"))
        cli.cmd_rig_check(None, argparse.Namespace(paths=[path], sets=[], print=False))
        out = capsys.readouterr().out
        assert "ok" in out and "1 devices" in out
