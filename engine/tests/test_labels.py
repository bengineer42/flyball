"""Labels (D-086): every one may be blank, and a blank one resolves to the key, humanised.

The engine resolves them -- a device, namespace, signal, input, command, controller and the
rig each have a `label` that is never empty -- and keeps what was declared apart, so a rig
rendered back to a file never gains the fallback. The wire carries the resolved one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from conftest import TestClient
from flyball.foundation.device import Committable, Namespace, Readout, command
from flyball.foundation.device.descriptors import Input
from flyball.foundation.keys import humanise
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Percent
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.deps import set_store
from flyball.record.sqlite import SqliteStore
from flyball.runtime.config import RigConfig, rig_schema

DUTY = Quantity("duty", Percent)


class Pump(Committable):
    """A pump with one labelled demand and one bare, a namespace, an input and two commands."""

    lines = Namespace("pump_lines")
    dry_pump_flow = lines.demand("dry_pump_flow", "", DUTY, limits=(0.0, 100.0))
    wet = lines.demand("wet", "Wet line", DUTY, limits=(0.0, 100.0))
    level = Readout("tvoc", "TVOC")
    supply = Input("supply_rh", "", DUTY)

    @command
    def prime_pump(self) -> None:
        """Prime it."""

    @command(label="Purge the lines")
    def purge(self) -> None:
        """Purge it."""


def _oven(**extra: object) -> dict:
    """A simulated oven: a thermometer, a heater and a controller on the heater's drive."""
    document: dict = {
        "name": "test_oven",
        "links": {"plant": {"type": "sim_plant", "model": "fopdt", "tau_s": 60.0, "gain": 80.0}},
        "devices": {
            "thermo": {
                "driver": "sim_daq",
                "link": "plant",
                "ports": {"oven_temp": {"port": "output", "quantity": "temperature", "unit": "°C"}},
            },
            "heater": {
                "driver": "sim_drive",
                "link": "plant",
                "ports": {
                    "drive": {
                        "port": "input",
                        "demand": "output",
                        "quantity": "temperature",
                        "unit": "°C",
                    }
                },
            },
        },
        "controllers": {
            "heater.drive": {
                "measured": "thermo.oven_temp",
                "law": {"type": "pi", "kp": 0.02, "ki": 0.0005},
            }
        },
    }
    document.update(extra)
    return document


class TestHumanise:
    @pytest.mark.parametrize(
        ("key", "label"),
        [
            ("dry_pump_flow", "Dry pump flow"),
            ("tvoc", "Tvoc"),
            ("poll_s", "Poll s"),
            ("wet-pump", "Wet pump"),
            ("a__b", "A b"),
            ("x", "X"),
            ("already Spaced", "Already Spaced"),
            ("", ""),
        ],
    )
    def test_sentence_case_the_rest_as_written(self, key, label):
        assert humanise(key) == label


class TestResolved:
    def test_a_blank_label_is_the_name_humanised_a_declared_one_stands(self):
        pump = Pump("main_pump")
        assert (pump.label, pump.declared_label) == ("Main pump", None)
        assert pump.root.label == "Main pump" and pump.root.declared_label == ""
        lines = pump.nodes["pump_lines"]
        assert (lines.label, lines.declared_label) == ("Pump lines", "")
        dry = pump.signals["pump_lines.dry_pump_flow"]
        assert (dry.label, dry.declared_label) == ("Dry pump flow", "")
        wet = pump.signals["pump_lines.wet"]
        assert (wet.label, wet.declared_label) == ("Wet line", "Wet line")
        assert pump.signals["tvoc"].label == "TVOC", "the driver says how an acronym reads"
        supply = pump.bound["supply_rh"]
        assert (supply.label, supply.declared_label) == ("Supply rh", "")

    def test_a_device_label_given_resolves_to_itself(self):
        assert Pump("p", "The pump").label == "The pump"
        assert Pump("p", "").declared_label is None, "blank is none"

    def test_a_command_is_labelled_like_everything_else(self):
        commands = Pump("p").commands
        assert commands["prime_pump"].label == "Prime pump"
        assert commands["prime_pump"].declared_label == ""
        assert commands["purge"].label == "Purge the lines"
        setter = commands["set_pump_lines_dry_pump_flow"]
        assert setter.label == "Set pump lines dry pump flow"
        assert setter.doc == "Set Dry pump flow."
        assert Pump("p").signals["last.purge"].label == "Purge the lines"
        assert Pump("p").signals["last.prime_pump"].label == "Prime pump"

    def test_the_rig_file_labels_a_signal_the_rig_and_a_controller(self):
        document = _oven(label="The test oven")
        document["devices"]["thermo"]["label"] = "Thermometer"
        document["devices"]["thermo"]["signals"] = {"oven_temp": {"label": "Oven"}}
        document["controllers"]["heater.drive"]["label"] = "Oven loop"
        rig = RigConfig.model_validate(document).build(start=False)
        try:
            assert rig.label == "The test oven"
            assert rig.devices["thermo"].label == "Thermometer"
            assert rig.devices["heater"].label == "Heater"
            assert rig.devices["thermo"].signals["oven_temp"].label == "Oven"
            assert rig.controllers["heater.drive"].label == "Oven loop"
            rendered = rig.document()
            assert rendered["label"] == "The test oven"
            assert rendered["controllers"]["heater.drive"]["label"] == "Oven loop"
            assert rendered["devices"]["thermo"]["label"] == "Thermometer"
        finally:
            rig.close()

    def test_a_rendered_rig_never_gains_a_fallback(self):
        rig = RigConfig.model_validate(_oven()).build(start=False)
        try:
            assert rig.label == "Test oven"
            controller = rig.controllers["heater.drive"]
            assert controller.declared_label is None
            assert controller.label == "Drive", "none declared: its output signal's"
            rendered = rig.document()
            assert "label" not in rendered
            assert "label" not in rendered["controllers"]["heater.drive"]
            assert all("label" not in entry for entry in rendered["devices"].values())
            assert "Test oven" not in str(rendered) and "Heater" not in str(rendered)
        finally:
            rig.close()


class TestWire:
    @pytest.fixture
    def client(self, tmp_path):
        document = _oven()
        document["devices"]["thermo"]["label"] = "Thermometer"
        rig = RigConfig.model_validate(document).build(start=False)
        store = SqliteStore(tmp_path / "t.db")
        set_rig(rig)
        set_store(store)
        with TestClient(create_app()) as c:
            yield c
        set_rig(None)
        set_store(None)
        store.close()
        rig.close()

    def test_every_label_on_the_wire_is_a_non_empty_string(self, client):
        devices = {d["name"]: d for d in client.get("/api/devices").json()}
        assert devices["thermo"]["label"] == "Thermometer"
        assert devices["heater"]["label"] == "Heater"

        def labels(tree):
            for node in tree:
                yield node["label"]
                yield from labels(node.get("signals", []) if "atomic" in node else [])

        for device in devices.values():
            found = [*labels(device["signals"])]
            found += [c["label"] for c in device["commands"]]
            found += [i["label"] for i in device["inputs"].values()]
            assert found and all(isinstance(x, str) and x for x in found), found
        (controller,) = client.get("/api/controllers").json()
        assert controller["label"] == "Drive"
        assert client.get("/api/health").json()["label"] == "Test oven"
        schema = client.get("/api/devices/heater/schema").json()
        assert schema["label"] == "Heater"
        assert schema["commands"]["set_drive"]["label"] == "Set drive"

    def test_a_dashboard_row_resolves_its_label_and_its_document_keeps_none(self, client):
        document = {"name": "x", "rig": "test_oven"}
        saved = client.put("/api/dashboards/wall_display", json=document).json()
        assert saved["label"] == "Wall display" and saved["body"]["label"] is None
        (row,) = client.get("/api/dashboards").json()
        assert row["label"] == "Wall display"
        client.put("/api/dashboards/wall_display", json={**document, "label": "The wall"})
        assert client.get("/api/dashboards/wall_display").json()["label"] == "The wall"


class TestSchemaTitles:
    def test_an_untitled_field_is_titled_by_the_same_rule(self):
        schema = rig_schema()
        entry = schema["$defs"]["ControllerEntry"]["properties"]
        assert entry["min_period_s"]["title"] == "Min period s"
        assert entry["is_default"]["title"] == "Is default"
        assert schema["properties"]["recording"]["title"] == "Recording"

    def test_every_schema_flyball_generates_goes_through_titled(self):
        """A `json_schema(` call without the generator would title in Title Case again."""
        source = Path(__file__).parent.parent / "src" / "flyball"
        # The class-definition check (`_schemable`) and a subprocess template only ask
        # whether a schema can be made; neither is served.
        exempt = {"foundation/device/commands.py", "interfaces/mcp/tools.py"}
        missing = []
        for path in source.rglob("*.py"):
            if str(path.relative_to(source)) in exempt:
                continue
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"([\w.]*json_schema)\(([^()]*)\)", text):
                callee, arguments = match.groups()
                if callee == "RigConfig.model_json_schema":
                    continue  # its override defaults the generator
                if "schema_generator" not in arguments and "**kwargs" not in arguments:
                    missing.append(f"{path.relative_to(source)}: {match.group(0)}")
        assert missing == []
