"""Loop management over HTTP: schema by unit, make, regulate, manual, reference, remove."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flyball.core.reading import Measurand, Source
from flyball.core.units.si import Celsius, Litre, Minute
from flyball.runtime.rig import Rig
from flyball.server import create_app, set_rig
from flyball.sim import FunctionReader, RecordingActuator


class Heater(RecordingActuator):
    demand_unit = Celsius


@pytest.fixture
def client():
    Measurand.forget("lt_temp")
    Measurand.forget("lt_flow")
    rig = Rig()
    temp = Measurand("lt_temp", Celsius)
    flow = Measurand("lt_flow", Litre / Minute)
    src = Source(f"lt_src_{id(rig)}", [temp, flow])
    rig.readers.add(FunctionReader("lt_reader", {src: lambda t: {temp: 20.0, flow: 1.0}}))
    rig.add_actuator(Heater("heater"))
    rig.add_actuator(RecordingActuator("anything"))
    set_rig(rig)
    with TestClient(create_app()) as c:
        yield c, rig, src
    set_rig(None)


def test_schema_offers_every_channel_and_the_feedforwards(client):
    c, rig, src = client
    rig.read(rig.readers.by_name["lt_reader"])  # sources are known once read
    schema = c.get("/api/loops/schema").json()
    by_name = {a["name"]: a for a in schema["actuators"]}
    assert by_name["heater"]["demand_unit"] == "°C" and by_name["anything"]["demand_unit"] is None
    every = sorted([f"{src.name}.lt_flow", f"{src.name}.lt_temp"])
    assert sorted(by_name["heater"]["channels"]) == every  # a feedforward bridges units
    assert sorted(by_name["anything"]["channels"]) == every
    tags = {d["properties"]["tag"]["const"] for d in schema["laws"]["$defs"].values()}
    assert "PI" in tags and schema["laws"]["discriminator"]["propertyName"] == "tag"
    ff = {d["properties"]["tag"]["const"] for d in schema["feedforwards"]["$defs"].values()}
    assert ff >= {"setpoint", "none", "affine", "table"}
    assert {c_["dimension"] for c_ in schema["channels"]} == {"Temperature", "Volume flow"}


def test_make_regulate_manual_reference_remove(client):
    c, rig, src = client
    rig.read(rig.readers.by_name["lt_reader"])
    bad = c.post(
        "/api/loops",
        json={"channel": f"{src.name}.lt_flow", "actuator": "heater", "feedforward": "setpoint"},
    )
    assert bad.status_code == 409  # heater takes °C; a flow setpoint cannot go straight to it
    ok = c.post("/api/loops", json={"channel": f"{src.name}.lt_flow", "actuator": "heater"})
    assert ok.status_code == 201 and ok.json()["feedforward"] == {"tag": "none"}
    assert ok.json()["demand_unit"] == "°C"
    assert c.delete("/api/loops/heater").status_code == 204

    made = c.post(
        "/api/loops",
        json={
            "channel": f"{src.name}.lt_temp",
            "actuator": "heater",
            "law": {"tag": "PI", "kp": 1.0, "ki": 0.0},
            "default": True,
        },
    )
    assert (
        made.status_code == 201
        and made.json()["mode"] == "manual"
        and made.json()["default"] is True
        and made.json()["feedforward"] == {"tag": "setpoint"}
    )
    assert (
        c.post(
            "/api/loops", json={"channel": f"{src.name}.lt_temp", "actuator": "anything"}
        ).status_code
        == 409
    )

    reg = c.post("/api/loops/heater/regulate", json={"at": 60.0})
    assert (
        reg.status_code == 200
        and reg.json()["mode"] == "regulating"
        and reg.json()["reference"] == 60.0
    )
    assert rig.actuators["heater"].demands  # the handover put a demand through

    ref = c.put("/api/loops/heater/reference", json={"at": 65.0})
    assert ref.json()["reference"] == 65.0 and ref.json()["mode"] == "regulating"

    man = c.post("/api/loops/heater/manual")
    assert man.json()["mode"] == "manual"

    assert c.delete("/api/loops/heater").status_code == 204
    assert c.get("/api/loops").json() == []
    assert c.delete("/api/loops/heater").status_code == 404


def test_every_actuator_can_be_driven_by_hand_unless_a_loop_regulates_it(client):
    c, rig, src = client
    rig.read(rig.readers.by_name["lt_reader"])
    schema = c.get("/api/actuators/heater/schema").json()
    assert "demand" in schema["commands"] and schema["commands"]["demand"]["simulation"] is False
    assert c.post("/api/actuators/heater/demand", json={"demand": 42.0}).status_code == 200
    assert rig.actuators["heater"].demands[-1] == 42.0

    c.post(
        "/api/loops",
        json={
            "channel": f"{src.name}.lt_temp",
            "actuator": "heater",
            "law": {"tag": "P", "kp": 1.0},
        },
    )
    c.post("/api/loops/heater/regulate", json={"at": 60.0})
    refused = c.post("/api/actuators/heater/demand", json={"demand": 42.0})
    assert refused.status_code == 409 and "stop the loop" in refused.json()["detail"]
    c.post("/api/loops/heater/manual")
    assert c.post("/api/actuators/heater/demand", json={"demand": 43.0}).status_code == 200
