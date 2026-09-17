"""The schema-driven client's validator."""

from __future__ import annotations

import pytest

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
