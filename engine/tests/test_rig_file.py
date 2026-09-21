"""The rig file's `devices:` and `controllers:` sections: parsing, `build()`, `rig check`."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from flyball.foundation.device import (
    Access,
    Committable,
    DriverConfig,
    NodeSpec,
    Readable,
    Role,
    Sample,
    Signal,
    SignalSpec,
)
from flyball.foundation.errors import ConflictError, NotFoundError
from flyball.foundation.files import loads
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Percent, Watt
from flyball.runtime.config import RigConfig, canonical, resolve_documents, rig_schema

TEMP = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)
HUMIDITY = Quantity("humidity", Percent)

# region Test-only drivers: a DAQ (RP signals) and a heater bank (W signals), the furnace
# shape from the plan's §2 example; a namespaced sensor set and a bound blender, the
# humidity rig's shape.


class Daq(Readable):
    """A multi-zone thermocouple DAQ: `zone1..N`, all `[RP]`."""

    def __init__(self, name: str, zones: int, label: str | None = None) -> None:
        super().__init__(name, label)
        self.bind([
            SignalSpec(name=f"zone{i}", quantity=TEMP, access=Access.RP, range=(0.0, 1200.0))
            for i in range(1, zones + 1)
        ])

    def read(self, time_ns: int, node=None) -> Iterator[Sample]:
        temps = {s: 20.0 for s in self.publishing.values() if s is not self.conditions}
        yield Sample(self.root, time_ns, temps)


class DaqConfig(DriverConfig[Daq]):
    zones: int = 3

    def build(self, name: str, label: str | None = None) -> Daq:
        return Daq(name, zones=self.zones, label=label)


class Heaters(Committable):
    """A relay bank: `heater1..N`, all demands."""

    def __init__(
        self, name: str, zones: int, limits: tuple[float, ...], label: str | None = None
    ) -> None:
        super().__init__(name, label)
        self.bind([
            SignalSpec(
                name=f"heater{i}",
                quantity=POWER,
                role=Role.DEMAND,
                access=Access.RPW,
                limits=(0.0, limits[i - 1]),
            )
            for i in range(1, zones + 1)
        ])


class HeatersConfig(DriverConfig[Heaters]):
    zones: int = 3
    limits: tuple[float, ...] = (2500.0, 6000.0, 2000.0)

    def build(self, name: str, label: str | None = None) -> Heaters:
        return Heaters(name, zones=self.zones, limits=self.limits, label=label)


def _sensor(name: str) -> NodeSpec:
    return NodeSpec(
        name=name,
        atomic=True,
        children=(
            SignalSpec(name="humidity", quantity=HUMIDITY, access=Access.RP, range=(0.0, 100.0)),
        ),
    )


class HumSensors(Readable):
    """Three humidity namespaces on one device -- today's `HTSetReader`, as a static tree."""

    TREE = (_sensor("chamber"), _sensor("dry"), _sensor("wet"))

    def read(self, time_ns: int, node=None) -> Iterator[Sample]:
        yield from ()


class HumSensorsConfig(DriverConfig[HumSensors]):
    def build(self, name: str, label: str | None = None) -> HumSensors:
        return HumSensors(name, label=label)


class Blender(Committable):
    """A settable humidity target, following two bound supply signals."""

    TREE = (
        SignalSpec(
            name="humidity",
            quantity=HUMIDITY,
            role=Role.DEMAND,
            access=Access.RPW,
            limits=(0.0, 100.0),
        ),
    )


class BlenderConfig(DriverConfig[Blender]):
    def build(self, name: str, label: str | None = None) -> Blender:
        return Blender(name, label=label)


@pytest.fixture
def daq_tag(fresh, _catalog) -> str:
    tag = fresh("eurotherm_daq")

    class Tagged(DaqConfig, tag=tag):
        pass

    _catalog.register_device(Tagged)
    return tag


@pytest.fixture
def sim_daq_tag(fresh, _catalog) -> str:
    """A second driver with the same shape as `daq_tag`'s: an overlay swapping the driver."""
    tag = fresh("sim_daq")

    class Tagged(DaqConfig, tag=tag):
        pass

    _catalog.register_device(Tagged)
    return tag


@pytest.fixture
def heaters_tag(fresh, _catalog) -> str:
    tag = fresh("ssr_bank")

    class Tagged(HeatersConfig, tag=tag):
        pass

    _catalog.register_device(Tagged)
    return tag


@pytest.fixture
def sensors_tag(fresh, _catalog) -> str:
    tag = fresh("sht4x_set")

    class Tagged(HumSensorsConfig, tag=tag):
        pass

    _catalog.register_device(Tagged)
    return tag


@pytest.fixture
def blender_tag(fresh, _catalog) -> str:
    tag = fresh("dual_pump_blender")

    class Tagged(BlenderConfig, tag=tag):
        pass

    _catalog.register_device(Tagged)
    return tag


# endregion


class TestParsing:
    def test_flat_and_layered_devices_parse_to_the_same_entry(self, daq_tag):
        flat = RigConfig.model_validate({"devices": {"f": {"driver": daq_tag, "zones": 2}}})
        layered = RigConfig.model_validate({
            "devices": {"f": {"driver": daq_tag, "config": {"zones": 2}}}
        })
        assert flat.devices == layered.devices

    def test_the_plan_s_furnace_example_parses(self, daq_tag, heaters_tag):
        document = {
            "name": "furnace",
            "devices": {
                "furnace": {
                    "driver": daq_tag,
                    "label": "Tube furnace",
                    "poll_s": 1,
                    "zones": 3,
                    "signals": {"zone1": {"label": "Zone 1 (entry)", "warn": [0, 1100]}},
                },
                "heaters": {
                    "driver": heaters_tag,
                    "label": "Zone heaters",
                    "zones": 3,
                    "limits": [2500, 6000, 2000],
                },
            },
            "controllers": {
                "heaters.heater1": {
                    "signal": "furnace.zone1",
                    "law": {"tag": "PI", "kp": 100, "ki": 0.15, "tt": 30},
                },
                "heaters.heater2": {
                    "signal": "furnace.zone2",
                    "law": {"tag": "PI", "kp": 100, "ki": 0.15, "tt": 30},
                    "default": True,
                },
            },
        }
        config = RigConfig.model_validate(document)
        assert set(config.devices) == {"furnace", "heaters"}
        assert set(config.controllers) == {"heaters.heater1", "heaters.heater2"}
        assert config.controllers["heaters.heater2"].default is True

    def test_the_plan_s_humidity_example_parses(self, sensors_tag, blender_tag):
        document = {
            "name": "humidity",
            "devices": {
                "hum_sensors": {"driver": sensors_tag, "label": "Humidity sensors", "poll_s": 1},
                "blender": {
                    "driver": blender_tag,
                    "label": "Pump blender",
                    "bound": {"dry": "hum_sensors.dry.humidity", "wet": "hum_sensors.wet.humidity"},
                },
            },
            "controllers": {
                "blender.humidity": {
                    "signal": "hum_sensors.chamber.humidity",
                    "law": {"tag": "PI", "kp": 0.8, "ki": 0.02, "tt": 60},
                    "default": True,
                }
            },
        }
        config = RigConfig.model_validate(document)
        assert config.devices["blender"].bound == {
            "dry": "hum_sensors.dry.humidity",
            "wet": "hum_sensors.wet.humidity",
        }


class TestChecks:
    def test_the_legacy_sections_are_refused_naming_the_plan(self, daq_tag):
        for section in ("readers", "actuators", "loops"):
            document = {"devices": {"x": {"driver": daq_tag, "zones": 1}}, section: []}
            with pytest.raises(
                ValueError,
                match="readers/actuators/loops are no longer rig-file sections; devices and"
                r" controllers replace them, see temp-docs/DEVICE-MODEL-PLAN.md §6",
            ):
                RigConfig.model_validate(document)

    def test_an_unknown_driver_or_a_link_as_driver_is_refused_before_build(self):
        with pytest.raises(ValueError, match="device 'x': driver 'nope' is not registered"):
            RigConfig.model_validate({"devices": {"x": {"driver": "nope"}}})
        # `sim_plant` is a link, not a device: devices and links are separate
        # `Catalog`s now, so its tag is simply not a registered device tag.
        with pytest.raises(ValueError, match="device 'x': driver 'sim_plant' is not registered"):
            RigConfig.model_validate({"devices": {"x": {"driver": "sim_plant"}}})

    def test_a_reserved_device_name_is_refused(self, daq_tag):
        with pytest.raises(ConflictError, match="Name 'schema' is reserved as a route segment"):
            RigConfig.model_validate({"devices": {"schema": {"driver": daq_tag}}})

    def test_two_default_controllers_are_refused(self, daq_tag, heaters_tag):
        document = {
            "devices": {"f": {"driver": daq_tag}, "h": {"driver": heaters_tag}},
            "controllers": {
                "h.heater1": {"signal": "f.zone1", "default": True},
                "h.heater2": {"signal": "f.zone2", "default": True},
            },
        }
        with pytest.raises(ValueError, match="only one controller can be the default"):
            RigConfig.model_validate(document)

    def test_a_clock_needs_a_simulated_rig(self, daq_tag):
        document = {
            "links": {"bench": {"tag": "visa", "resource": "x"}},
            "devices": {"f": {"driver": daq_tag}},
            "clock": {"speed": 2},
        }
        with pytest.raises(ValueError, match="`clock` is only for a rig whose links"):
            RigConfig.model_validate(document)
        document["links"] = {"p": {"tag": "sim_plant"}}
        assert RigConfig.model_validate(document).simulated is True

    def test_a_device_s_undeclared_link_is_refused(self, daq_tag):
        document = {"devices": {"f": {"driver": daq_tag, "zones": 1, "link": "nowhere"}}}
        with pytest.raises(ValueError, match="link 'nowhere' is not declared"):
            RigConfig.model_validate(document)

    def test_a_controller_address_without_a_dot_is_refused(self, daq_tag):
        document = {
            "devices": {"f": {"driver": daq_tag, "zones": 1}},
            "controllers": {"heater1": {"signal": "f.zone1"}},
        }
        with pytest.raises(ValueError, match="'heater1' must be a 'node.signal' address"):
            RigConfig.model_validate(document)
        document["controllers"] = {"f.zone1": {"signal": "nodot"}}
        with pytest.raises(ValueError, match="signal 'nodot' must be a 'node.signal' address"):
            RigConfig.model_validate(document)

    def test_two_controllers_on_one_target_is_refused_by_the_strict_loader(self):
        """A repeat target key is a duplicate key.

        Caught before a `RigConfig` ever sees it, exactly like a duplicate
        device or reader name.
        """
        text = "controllers:\n  f.heater1: {signal: g.zone1}\n  f.heater1: {signal: g.zone2}\n"
        with pytest.raises(ValueError, match="duplicate key"):
            loads(text, ".yaml")

    def test_canonical_round_trips_through_model_validate(self, daq_tag):
        document = {"name": "x", "devices": {"f": {"driver": daq_tag, "zones": 2}}}
        config = RigConfig.model_validate(document)
        dumped = canonical(config)
        assert dumped["devices"]["f"]["config"] == {"zones": 2}
        assert RigConfig.model_validate(dumped) == config

    def test_schema_describes_flat_and_layered_per_driver(self, daq_tag, heaters_tag):
        schema = rig_schema()
        by_driver = schema["properties"]["devices"]["additionalProperties"]
        assert "oneOf" in by_driver
        tags = {
            shape["properties"]["driver"]["const"]
            for variant in by_driver["oneOf"]
            for shape in variant["oneOf"]
        }
        assert {daq_tag, heaters_tag} <= tags


class TestBuild:
    def test_build_wires_devices_bound_inputs_and_controllers(self, sensors_tag, blender_tag):
        document = {
            "devices": {
                "hum_sensors": {"driver": sensors_tag},
                "blender": {"driver": blender_tag, "bound": {"dry": "hum_sensors.dry.humidity"}},
            },
            "controllers": {
                "blender.humidity": {
                    "signal": "hum_sensors.chamber.humidity",
                    "law": {"tag": "PI", "kp": 0.8, "ki": 0.02},
                }
            },
        }
        rig = RigConfig.model_validate(document).build(start=False)
        target = rig.resolve("blender.humidity")
        assert isinstance(target, Signal)
        assert "blender.humidity" in rig.controllers
        controller = rig.controllers["blender.humidity"]
        assert controller.target is target
        assert controller.source is rig.resolve("hum_sensors.chamber.humidity")
        dry = rig.resolve("hum_sensors.dry.humidity")
        assert rig.devices["blender"].bound["dry"] is dry

    def test_build_refuses_a_controller_on_an_unknown_address(self, daq_tag):
        document = {
            "devices": {"f": {"driver": daq_tag, "zones": 1}},
            "controllers": {"f.nope": {"signal": "f.zone1"}},
        }
        with pytest.raises(NotFoundError, match="nope"):
            RigConfig.model_validate(document).build(start=False)

    def test_furnace_zone1_resolves_to_a_signal(self, daq_tag, heaters_tag):
        document = {
            "devices": {
                "furnace": {"driver": daq_tag, "zones": 2},
                "heaters": {"driver": heaters_tag, "zones": 2, "limits": [2500, 6000]},
            },
            "controllers": {
                "heaters.heater1": {"signal": "furnace.zone1", "law": {"tag": "PI", "kp": 1.0}}
            },
        }
        rig = RigConfig.model_validate(document).build(start=False)
        assert isinstance(rig.resolve("furnace.zone1"), Signal)
        assert "heaters.heater1" in rig.controllers


class TestOverlay:
    def test_an_overlay_swaps_the_driver_behind_the_same_name(self, tmp_path, daq_tag, sim_daq_tag):
        base = tmp_path / "furnace.yaml"
        base.write_text(f"devices:\n  furnace: {{driver: {daq_tag}, zones: 2}}\n")
        overlay = tmp_path / "sim.yaml"
        overlay.write_text(f"devices:\n  furnace: {{driver: {sim_daq_tag}, zones: 2}}\n")
        document, files = resolve_documents([base, overlay])
        rig = RigConfig.model_validate(document).build(start=False)
        assert len(files) == 2
        assert list(rig.devices) == ["furnace"]
