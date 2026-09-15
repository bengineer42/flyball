"""The HTTP and websocket surface, through FastAPI's test client."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flyball.core.reading import Reader
from flyball.core.signal import Signal
from flyball.programmer.activites import Wait
from flyball.server import create_app, set_rig
from helpers import sample


@pytest.fixture
def client(rig, duty_heater, probe, temperature, fresh):
    class Probe(Reader):
        """A pretend sensor."""

        def __init__(self):
            super().__init__(fresh("probe_reader"), (probe,))

        def read(self, time_ns):
            return [sample(probe, temperature, 21.5, time_ns)]

    rig.add_actuator(duty_heater)
    rig.readers.add(Probe())
    set_rig(rig)
    with TestClient(create_app()) as c:
        yield c
    set_rig(None)


def test_schema_lists_every_device(client, duty_heater):
    schema = client.get("/api/schema").json()
    heater = schema["actuators"][duty_heater.name]
    assert (
        heater["type"] == "DutyHeater"
        and heater["description"] == "A heater with one command, for device and route tests."
    )
    assert set(heater["commands"]) == {"set_duty", "off"}
    assert heater["commands"]["set_duty"]["arguments"]["properties"]["duty"]["type"] == "number"
    (reader,) = schema["readers"].values()
    (source,) = reader["sources"]
    assert source["measurands"][next(iter(source["measurands"]))]["range"] == [-40.0, 125.0]


def test_actuator_view_and_commands(client, duty_heater):
    name = duty_heater.name
    assert client.get(f"/api/actuators/{name}").json()["state"]["duty"] == 0.0
    r = client.post(f"/api/actuators/{name}/set_duty", json={"duty": 0.4})
    assert r.status_code == 200 and r.json()["duty"] == 0.4
    assert client.post(f"/api/actuators/{name}/off").status_code == 200
    assert client.get(f"/api/actuators/{name}").json()["state"]["duty"] == 0.0


def test_command_validation_and_lookup_errors(client, duty_heater):
    name = duty_heater.name
    assert client.post(f"/api/actuators/{name}/set_duty", json={"duty": "x"}).status_code == 422
    assert client.post(f"/api/actuators/{name}/set_duty", json={"bogus": 1}).status_code == 422
    assert client.post(f"/api/actuators/{name}/explode").status_code == 404
    assert client.get("/api/actuators/nope").status_code == 404


def test_channel_carries_range_and_precision(client, probe, temperature):
    channel = client.get(f"/api/sources/{probe.name}").json()["channels"][0]
    assert (
        channel["unit"] == "°C" and channel["range"] == [-40.0, 125.0] and channel["precision"] == 2
    )


def test_signals_can_be_listed_fired_and_interrupted(client, rig):
    prompt = Wait("Close the lid").run(rig)
    rig.signals.register("lid", prompt, prompt.message)
    assert client.get("/api/signals").json()["lid"]["outcome"] == "pending"
    assert client.post("/api/signals/lid/fire").json() == {"name": "lid", "fired": True}
    assert prompt.fired
    assert client.post("/api/signals/nope/fire").status_code == 404
    other = Signal()
    rig.signals.register("other", other)
    assert (
        client.post("/api/signals/other/interrupt").json()["interrupted"] is True
        and other.interrupted
    )


def test_actuator_websocket_sends_a_snapshot_then_changes(client, rig, duty_heater):
    with client.websocket_connect("/ws/actuators") as ws:
        first = ws.receive_json()
        assert first["actuators"][0]["name"] == duty_heater.name
        duty_heater.set_duty(0.9)
        rig.apply(duty_heater)
        frame = ws.receive_json()
        assert frame["actuators"][0]["state"]["duty"] == 0.9


def test_reader_routes(client, rig):
    (name,) = rig.readers.by_name
    view = client.get(f"/api/readers/{name}").json()
    assert view["run"]["running"] is False and view["view"]["state"]["conditions"] == []
    assert client.get(f"/api/readers/{name}/schema").json()["type"] == "Probe"


def test_clock_reports_the_rig_s_timebase(client, rig, clock):
    clock.advance(2.5)
    rig.clock.tag("run")
    clock.advance(0.5)
    body = client.get("/api/clock").json()
    assert body["start_time_ns"] == rig.clock.start_time_ns
    assert body["now_ns"] == rig.clock.now_ns()
    assert body["elapsed_ns"] >= 0 and "run" in body["tags"]
