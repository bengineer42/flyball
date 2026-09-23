"""Rig-file semantics: YAML booleans, explicit nulls, limits that only narrow, refusals."""

from __future__ import annotations

import pytest

from flyball.foundation.device import (
    Committable,
    Demand,
    DeviceEntry,
    DriverConfig,
    Output,
    Sample,
)
from flyball.foundation.files import loads
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Percent, Watt
from flyball.interfaces.server.formats import parse
from flyball.runtime.overlay import parse_set

POWER = Quantity("power", Watt)
HUMIDITY = Quantity("humidity", Percent)


class Blender(Committable):
    """A heater with fixed limits, a free demand, and a humidity bounded by two supplies."""

    dry = Output("dry", "Dry supply humidity", HUMIDITY)
    wet = Output("wet", "Wet supply humidity", HUMIDITY)
    heater = Demand("heater", "Heater", POWER, limits=(0.0, 2500.0))
    free = Demand("free", "Unbounded demand", POWER)
    humidity = Demand("humidity", "Target humidity", HUMIDITY, limits=(dry, wet))


class BlenderConfig(DriverConfig[Blender]):
    def build(self, name: str, label: str | None = None) -> Blender:
        return Blender(name, label=label)


@pytest.fixture
def blender_tag(fresh, _catalog) -> str:
    tag = fresh("wf0_blender")

    class Tagged(BlenderConfig, tag=tag):
        pass

    _catalog.register_device(Tagged)
    return tag


@pytest.fixture
def build(blender_tag, fresh):
    def _build(signals: dict) -> Blender:
        entry = DeviceEntry.model_validate({"driver": blender_tag, "signals": signals})
        device = entry.build(fresh("blender"))
        assert isinstance(device, Blender)
        return device

    return _build


def _read(rig, device: Blender, **values: float) -> None:
    signals = device.signals
    rig.on_samples([
        Sample(device.root, rig.clock.now_ns(), {signals[k]: v for k, v in values.items()})
    ])


class TestYamlBooleans:
    @pytest.mark.parametrize("word", ["on", "off", "yes", "no", "On", "OFF", "Yes", "NO"])
    def test_yaml_1_1_words_stay_strings(self, word):
        assert loads(f"on_stop: {word}", ".yaml") == {"on_stop": word}
        assert loads(f"{word}: 1", ".yaml") == {word: 1}, "a key, not True/False"

    def test_the_gpio_case(self):
        doc = loads("signals:\n  on: {label: Lamp}\ncommands: [on, off]\n", ".yaml")
        assert doc == {"signals": {"on": {"label": "Lamp"}}, "commands": ["on", "off"]}

    @pytest.mark.parametrize(
        ("word", "value"),
        [("true", True), ("True", True), ("TRUE", True), ("false", False), ("FALSE", False)],
    )
    def test_true_and_false_are_still_booleans(self, word, value):
        assert loads(f"x: {word}", ".yaml") == {"x": value}

    def test_the_other_yaml_readers_agree(self):
        assert parse("on_stop: off", "yaml") == {"on_stop": "off"}
        assert parse_set("devices.x.config.on_stop=off") == (
            ["devices", "x", "config", "on_stop"],
            "off",
        )
        assert parse_set("a=true")[1] is True

    def test_the_duplicate_key_check_is_kept(self):
        with pytest.raises(ValueError, match="duplicate key 'on'"):
            loads("on: 1\non: 2\n", ".yaml")
