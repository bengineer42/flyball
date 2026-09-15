"""The HTTP and websocket surface, through FastAPI's test client."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flyball.core.reading import Reader
from flyball.core.signal import Signal
from flyball.programmer.activities import Wait
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
    assert set(heater["commands"]) == {"demand", "set_duty", "off"}
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


def test_health_is_one_look_at_the_rig(client, rig, duty_heater):
    body = client.get("/api/health").json()
    assert body["ok"] is True and body["recording"] is False
    assert body["loops"] == {} and body["conditions"] == []
    assert set(body["readers"]) == set(rig.readers.by_name)


def test_health_without_a_rig_says_so():
    set_rig(None)
    with TestClient(create_app()) as c:
        assert c.get("/api/health").json() == {"ok": False, "rig": None}


def test_events_are_kept_and_streamed(client, rig):
    from flyball.core.device import Level

    rig.event(Level.WARNING, "reader", "probe", "slow", "took 2 s", {"took_s": 2.0})
    rig.event(Level.INFO, "rig", "x", "note", "quiet")
    events = client.get("/api/events").json()
    assert [e["level"] for e in events] == ["WARNING", "INFO"]
    assert events[0]["details"] == {"took_s": 2.0} and events[0]["time_ns"] == rig.clock.now_ns()
    assert [e["kind"] for e in client.get("/api/events?level=warning").json()] == ["slow"]
    assert len(client.get("/api/events?limit=1").json()) == 1
    with client.websocket_connect("/ws/events") as ws:
        assert [e["kind"] for e in ws.receive_json()["events"]] == ["slow", "note"]
        rig.event(Level.ERROR, "program", "bake[2]", "step_failed", "no such actuator")
        (event,) = ws.receive_json()["events"]
        assert event["level"] == "ERROR" and event["subject"] == "bake[2]"


@pytest.fixture
def programmer(client, rig):
    from flyball.programmer import Programmer
    from flyball.server import set_programmer

    programmer = Programmer(rig)
    set_programmer(programmer)
    yield programmer
    programmer.interrupt()
    set_programmer(None)


def test_program_check_normalises_without_running(client, programmer):
    body = {"name": "t", "steps": [{"wait": "press go"}, {"wait": {"message": "m", "name": "n"}}]}
    checked = client.post("/api/programs/check", json=body).json()
    assert checked["steps"][0] == {"command": {"command": "wait", "message": "press go"}}
    assert checked["steps"][1]["command"]["name"] == "n"
    assert client.get("/api/programs/running").json()["running"] is False
    bad = client.post("/api/programs/check", json={"steps": [{"nope": 1}]})
    assert bad.status_code == 422 and "nope" in bad.json()["detail"]
    extra = client.post("/api/programs/check", json={"steps": [{"wait": {"message": "m", "x": 1}}]})
    assert extra.status_code == 422 and "x" in extra.json()["detail"]
    assert (
        "wait"
        in client.get("/api/programs/schema").json()["properties"]["steps"]["items"]["oneOf"][0][
            "properties"
        ]
    )


def test_program_runs_step_by_step_as_signals_are_answered(client, programmer, rig):
    body = {"name": "go", "steps": [{"wait": "one"}, {"wait": {"message": "two", "name": "two"}}]}
    state = client.post("/api/programs/run", json=body).json()
    assert state == {"running": True, "step": 0, "steps": 2, "command": "wait"}
    assert list(client.get("/api/signals").json()) == ["wait"]
    assert client.post("/api/signals/wait/fire").json()["fired"] is True
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and "two" not in rig.signals.states():
        time.sleep(0.01)
    assert client.get("/api/programs/running").json()["step"] == 1
    assert client.post("/api/programs/interrupt").json()["running"] is False
    programmer.join(2)
    assert rig.signals.states() == {}


def test_program_that_needs_no_waiting_finishes_at_once(client, programmer, rig):
    from dataclasses import dataclass

    from flyball.programmer import Command

    seen = []

    @dataclass(frozen=True)
    class Note(Command, tag="note", primary="text"):
        """Append to a list."""

        text: str

        def run(self, rig, operator=None):
            seen.append(self.text)
            return None

    state = client.post("/api/programs/run", json={"steps": [{"note": "a"}, {"note": "b"}]}).json()
    assert state["running"] is False and seen == ["a", "b"]


def test_program_with_an_unknown_step_is_refused_before_anything_runs(client, programmer):
    r = client.post("/api/programs/run", json={"steps": [{"wait": "ok"}, {"bogus": 1}]})
    assert r.status_code == 422
    assert client.get("/api/signals").json() == {}


class TestSimRoutes:
    @pytest.fixture
    def sim(self, client, tmp_path):
        from flyball.core.reading import Source
        from flyball.runtime.config import load_rig_config
        from flyball.runtime.simulation import Simulation
        from flyball.server import set_simulation

        path = Path(__file__).resolve().parents[2] / "examples" / "simulated" / "tank.toml"
        Source.forget("level")
        config = load_rig_config(path)
        rig = config.build(start=False)
        simulation = Simulation(rig, config, None, tmp_path / "tank.toml")
        set_rig(rig)
        set_simulation(simulation)
        yield simulation
        set_simulation(None)
        set_rig(None)
        Source.forget("level")

    def test_a_hardware_rig_says_it_is_not_simulated(self, client):
        assert client.get("/api/sim").json() == {"simulated": False}
        assert client.put("/api/sim/clock", json={"speed": 2}).status_code == 409

    def test_clock_plants_and_save(self, client, sim, tmp_path):
        state = client.get("/api/sim").json()
        assert state["simulated"] is True and list(state["plants"]) == ["tank"]
        assert client.put("/api/sim/clock", json={"speed": 20}).json() == {"speed": 20.0}
        assert client.get("/api/clock").json()["speed"] == 20.0
        assert client.put("/api/sim/plants/tank", json={"gain": 2.5}).json()["gain"] == 2.5
        assert client.put("/api/sim/plants/tank", json={"kind": "lag"}).status_code == 422
        assert client.put("/api/sim/plants/ghost", json={}).status_code == 404
        reset = client.post("/api/sim/plants/tank/reset", json={"output": 12.0}).json()
        assert reset["output"] == pytest.approx(12.0, abs=0.5)
        assert client.get("/api/sim/config").json()["links"]["tank"]["gain"] == 2.5
        saved = client.post("/api/sim/save").json()["path"]
        assert Path(saved) == tmp_path / "tank.toml" and (tmp_path / "tank.toml").exists()
        assert client.get("/api/sim").json()["changed"] == []

    def test_a_device_schema_s_live_links_resolve_against_its_view(self, client, sim):
        """`live` on a config field rides along in the schema; the path points into the view."""
        from flyball.runtime.simulation import resolve_live

        schema = client.get("/api/actuators/valve/schema").json()
        assert schema["config"]["properties"]["limits"]["live"] == "state.input"
        assert "live" not in schema["config"]["properties"]["port"]
        sim.rig.actuators["valve"].set_demand(500)  # far past what the valve can pass: clamped
        view = client.get("/api/actuators/valve").json()
        assert view["config"]["limits"] == [0.0, 1.0]
        assert resolve_live("state.input", view) == view["state"]["input"] == 1.0
