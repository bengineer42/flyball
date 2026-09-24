"""The key grammar (D-077) and `-`/`_` as one name (D-079), at every boundary a key enters."""

from __future__ import annotations

import pytest

from flyball.foundation.device import Access, Committable, NodeSpec, Role, SignalSpec
from flyball.foundation.keys import Keyed, canonical, check_address, check_key, check_keys
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Percent
from flyball.library.tunings import Tuning
from flyball.record.sqlite import SqliteStore
from flyball.runtime.config import RigConfig

DUTY = Quantity("duty", Percent)
NOT_KEYS = ("a.b", "", "Dry Pump", "x/y", "a/b", "my value", "1st", "é", "a" * 65)


def _oven(**names: str) -> dict:
    """The oven example, its names spelled as given: link, thermo, heater, rig."""
    link, thermo, heater = names["link"], names["thermo"], names["heater"]
    return {
        "name": names["rig"],
        "links": {
            link: {"type": "sim_plant", "model": "fopdt", "tau_s": 60.0, "gain": 80.0, "seed": 1}
        },
        "devices": {
            thermo: {
                "driver": "sim_daq",
                "link": names.get("thermo_link", link),
                "ports": {
                    "temperature": {"port": "output", "quantity": "temperature", "unit": "°C"}
                },
            },
            heater: {
                "driver": "sim_drive",
                "link": names.get("heater_link", link),
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
            f"{names.get('controller', heater)}.drive": {
                "measured": f"{names.get('measured', thermo)}.temperature",
                "law": {"type": "pi", "kp": 0.02, "ki": 0.0005},
                "is_default": True,
            }
        },
    }


class TestGrammar:
    @pytest.mark.parametrize("name", NOT_KEYS)
    def test_a_name_that_is_not_a_key_is_refused_naming_it(self, name):
        with pytest.raises(ValueError, match=f"device {name!r} is not a key"):
            check_key(name, "device")

    def test_either_spelling_gives_the_canonical_one(self):
        assert check_key("wet-pump") == check_key("wet_pump") == "wet_pump"
        assert check_address("wet-pump.flow-rate") == "wet_pump.flow_rate"
        assert canonical("a-b.c-d") == "a_b.c_d"
        assert check_key("z" * 64) == "z" * 64

    def test_two_names_that_differ_only_by_dash_and_underscore_collide(self):
        with pytest.raises(ValueError, match="'wet_pump' and 'wet-pump' are the same name"):
            check_keys({"wet-pump": 1, "wet_pump": 2}, "device")

    def test_keyed_finds_either_spelling(self):
        found = Keyed([("wet-pump", 1)])
        assert list(found) == ["wet_pump"]
        assert found["wet-pump"] == found["wet_pump"] == found.get("wet-pump") == 1
        assert "wet-pump" in found and found.pop("wet-pump") == 1 and not found


class TestSignals:
    @pytest.mark.parametrize("name", ["a/b", "my value", "a.b", "Temp"])
    def test_a_signal_name_that_is_not_a_key_is_refused(self, name):
        with pytest.raises(ValueError, match=f"signal {name!r} is not a key"):
            SignalSpec(name=name, quantity=DUTY, access=Access.RP)

    def test_a_tag_axis_and_value_are_keys(self):
        with pytest.raises(ValueError, match="tag axis 'Line' is not a key"):
            SignalSpec(name="t", quantity=DUTY, access=Access.RP, tags={"Line": "dry"})
        with pytest.raises(ValueError, match="tag 'line' value 'wet probe' is not a key"):
            SignalSpec(name="t", quantity=DUTY, access=Access.RP, tags={"line": "wet probe"})
        spec = SignalSpec(name="t", quantity=DUTY, access=Access.RP, tags={"probe-line": "a-b"})
        assert spec.tags == {"probe_line": "a_b"}

    def test_a_sibling_that_flattens_onto_a_set_command_is_refused(self):
        class Flows(Committable):
            def __init__(self, name: str) -> None:
                super().__init__(name)
                demand = {"quantity": DUTY, "access": Access.RPW, "role": Role.DEMAND}
                self.bind([
                    NodeSpec(name="flows", children=(SignalSpec(name="dry", **demand),)),
                    SignalSpec(name="flows_dry", **demand),
                ])

        with pytest.raises(ValueError, match="both make the command 'set_flows_dry'"):
            Flows("blender")


class TestRigFile:
    @pytest.mark.parametrize("name", ["a.b", "", "Dry Pump", "x/y"])
    def test_a_device_name_that_is_not_a_key_is_refused_naming_it(self, name):
        document = {"devices": {name: {"driver": "values"}}}
        with pytest.raises(ValueError, match=f"device {name!r} is not a key"):
            RigConfig.model_validate(document)

    def test_a_values_entry_and_a_link_are_keys(self):
        bench = {"driver": "values", "values": {"Dry supply": {"initial": 1}}}
        config = RigConfig.model_validate({"devices": {"bench": bench}})
        with pytest.raises(ValueError, match="value 'Dry supply' is not a key"):
            config.build(start=False)  # the driver's own fields are validated as it is built
        with pytest.raises(ValueError, match="link 'Chamber' is not a key"):
            RigConfig.model_validate({"links": {"Chamber": {"type": "sim_plant"}}})

    def test_a_controller_key_is_an_address_of_keys(self):
        with pytest.raises(ValueError, match="controller 'Heater.drive' is not an address"):
            RigConfig.model_validate({"controllers": {"Heater.drive": {"measured": "a.b"}}})

    def test_two_devices_that_differ_only_by_dash_and_underscore_collide(self):
        document = {"devices": {"wet-pump": {"driver": "values"}, "wet_pump": {"driver": "values"}}}
        with pytest.raises(ValueError, match="are the same name"):
            RigConfig.model_validate(document)

    def test_a_hyphened_rig_is_built_and_found_by_either_spelling(self):
        config = RigConfig.model_validate(
            _oven(
                rig="hot-box",
                link="oven-chamber",
                thermo="oven-thermo",
                thermo_link="oven_chamber",
                heater="oven-heater",
                controller="oven_heater",
                measured="oven-thermo",
            )
        )
        assert config.name == "hot_box"
        assert list(config.links) == ["oven_chamber"]
        assert list(config.devices) == ["oven_thermo", "oven_heater"]
        assert list(config.controllers) == ["oven_heater.drive"]
        assert config.controllers["oven_heater.drive"].measured == "oven_thermo.temperature"
        rig = config.build(start=False)
        try:
            assert rig.name == "hot_box"
            assert rig.resolve("oven-thermo.temperature") is rig.resolve("oven_thermo.temperature")
            assert rig.devices["oven-heater"] is rig.devices["oven_heater"]
            assert rig.controllers.resolve("oven-heater.drive").name == "oven_heater.drive"
            assert rig.controllers.default_controller == "oven_heater.drive"
            document = rig.document()
            assert document["name"] == "hot_box"
            assert set(document["devices"]) == {"oven_thermo", "oven_heater"}
        finally:
            rig.close()

    def test_a_board_name_is_a_key(self):
        with pytest.raises(ValueError, match="board 'My Board' is not a key"):
            RigConfig.model_validate({"board": "My Board"})


class TestResources:
    def test_programs_tunings_and_dashboards_are_named_by_keys(self, tmp_path):
        store = SqliteStore(tmp_path / "s.sqlite")
        try:
            saved = store.save_program("dry-then-hold", "yaml", "steps: []\n", 1)
            assert saved.name == "dry_then_hold"
            assert store.program("dry-then-hold").id == store.program("dry_then_hold").id
            store.save_program("dry_then_hold", "yaml", "steps: []\n# 2\n", 2)
            assert len(store.program_history("dry-then-hold")) == 2, "one name, two versions"
            with pytest.raises(ValueError, match="program name 'Dry Then Hold' is not a key"):
                store.save_program("Dry Then Hold", "yaml", "steps: []\n", 3)
            assert store.save_tuning("pid-aggressive", "pi", {}, 1).name == "pid_aggressive"
            assert store.tuning("pid-aggressive").name == "pid_aggressive"
            assert store.save_dashboard("the-wall", "oven", {}, 1).name == "the_wall"
            renamed = store.rename_dashboard("the-wall", "big-wall")
            assert {row.name for row in renamed} == {"big_wall"}
        finally:
            store.close()

    def test_a_tuning_is_named_by_a_key(self):
        from flyball.control.laws import OpenLoop

        assert Tuning("gentle-pi", OpenLoop.config_type()).name == "gentle_pi"
        with pytest.raises(ValueError, match="tuning 'Gentle PI' is not a key"):
            Tuning("Gentle PI", OpenLoop.config_type())
