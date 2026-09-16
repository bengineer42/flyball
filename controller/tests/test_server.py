"""The HTTP and websocket surface, through FastAPI's test client."""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from flyball.core.device import Device, DeviceState, Level, command
from flyball.core.quantity import Quantity
from flyball.core.signal import Access, Node, NodeSpec, Sample, Signal, SignalSpec, WriteState
from flyball.core.trigger import Trigger
from flyball.core.units.si import Celsius, Percent, Watt
from flyball.server import create_app, set_rig

TEMP = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)
HUMIDITY = Quantity("humidity", Percent)

# region Test drivers: a DAQ (RP zones and an RW setting) and a drive (W heaters), the
# furnace shape from the plan's §2 example, and a sensor set with atomic namespaces.


class Daq(Device):
    """Two thermocouple zones `[RP]` and a setting `[RW]`; counts the reads asked of it."""

    TREE = (
        SignalSpec(
            name="zone1",
            quantity=TEMP,
            access=Access.RP,
            label="Zone 1",
            range=(0.0, 1200.0),
            precision=1,
            warn=(0.0, 1100.0),
            alarm=(-10.0, 1150.0),
        ),
        SignalSpec(name="zone2", quantity=TEMP, access=Access.RP, warn=(0.0, 1100.0)),
        SignalSpec(name="setpoint", quantity=TEMP, access=Access.RW),
    )

    def __init__(self, name: str, label: str | None = None) -> None:
        super().__init__(name, label)
        self.temps = {"zone1": 21.5, "zone2": 22.0, "setpoint": 0.0}
        self.reads = 0
        self.broken = False

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        self.reads += 1
        if self.broken:
            raise OSError("open circuit")
        yield Sample(self.root, time_ns, {s: self.temps[s.name] for s in self.readable.values()})

    def commit(self, time_ns: int) -> Mapping[Signal, WriteState]:
        for signal, value in self.pending.items():
            self.temps[signal.name] = value
        return self.flush_pending()

    @command
    def restore(self) -> None:
        """Mend it."""
        self.broken = False


@dataclass(frozen=True, slots=True, kw_only=True)
class DriveState(DeviceState):
    duty: float = 0.0


class Drive(Device):
    """Two heaters `[W]` with limits, one command; keeps what it was told to put out."""

    TREE = (
        SignalSpec(
            name="heater1", quantity=POWER, access=Access.W, label="Heater 1", limits=(0.0, 2500.0)
        ),
        SignalSpec(name="heater2", quantity=POWER, access=Access.W, limits=(0.0, 6000.0)),
    )

    def __init__(self, name: str, label: str | None = None) -> None:
        super().__init__(name, label)
        self.inputs: dict[str, float] = {}
        self.duty = 0.0

    def write_signal(self, signal: Signal, value: float) -> None:
        self.inputs[signal.name] = value

    @property
    def state(self) -> DriveState:
        return DriveState(duty=self.duty)

    @command
    def set_duty(self, duty: float, ramp_s: float = 0.0) -> DriveState:
        """Drive the elements at a fixed duty."""
        self.duty = duty
        return self.state

    @command(tag="off", simulation=True)
    def switch_off(self) -> None:
        """Stop heating."""
        self.duty = 0.0


class Sensors(Device):
    """Two atomic namespaces, each read in its own transaction."""

    TREE = tuple(
        NodeSpec(
            name=name,
            atomic=True,
            children=(
                SignalSpec(name="humidity", quantity=HUMIDITY, access=Access.RP),
                SignalSpec(name="temperature", quantity=TEMP, access=Access.RP),
            ),
        )
        for name in ("chamber", "dry")
    )

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        nodes = self.root.descendants() if node is None or node is self.root else (node,)
        for n in nodes:
            yield Sample(n, time_ns, {n.signals["humidity"]: 45.0, n.signals["temperature"]: 21.9})


# endregion


@pytest.fixture
def daq(rig, fresh) -> Daq:
    daq = Daq(fresh("furnace"), label="Tube furnace")
    rig.add_device(daq)
    return daq


@pytest.fixture
def drive(rig, fresh) -> Drive:
    drive = Drive(fresh("heaters"))
    rig.add_device(drive)
    return drive


@pytest.fixture
def client(rig, daq, drive):
    set_rig(rig)
    with TestClient(create_app()) as c:
        yield c
    set_rig(None)


def deliver(rig, daq: Daq, time_ns: int | None = None) -> Sample:
    """One delivery of everything the DAQ reads, as a poll would make it."""
    (sample,) = daq.read(rig.clock.now_ns() if time_ns is None else time_ns)
    rig.on_samples([sample])
    return sample


# region Devices


def test_devices_list_the_tree_with_latest_values_and_write_states(client, rig, daq, drive):
    devices = {d["name"]: d for d in client.get("/api/devices").json()}
    assert set(devices) == {daq.name, drive.name}
    furnace = devices[daq.name]
    assert furnace["label"] == "Tube furnace" and furnace["type"] == "Daq"
    assert furnace["kind"] == "device"
    assert furnace["driver"] is None and furnace["link"] is None and furnace["run"] is None
    assert [s["name"] for s in furnace["signals"]] == ["zone1", "zone2", "setpoint"]
    zone1 = furnace["signals"][0]
    assert zone1 == {
        "name": "zone1",
        "address": f"{daq.name}.zone1",
        "access": "rp",
        "label": "Zone 1",
        "quantity": "temperature",
        "unit": "°C",
        "dimension": "Temperature",
        "dtype": "float",
        "shape": [],
        "range": [0.0, 1200.0],
        "precision": 1,
        "warn": [0.0, 1100.0],
        "alarm": [-10.0, 1150.0],
        "poll_s": None,
        "limits": None,
        "together": [],
        "latest": None,
        "write": None,
    }
    assert furnace["signals"][2]["access"] == "rw"
    assert furnace["commands"] == [
        {"name": "restore", "description": "Mend it.", "simulation": False}
    ]
    assert furnace["state"] == {"conditions": []} and furnace["conditions"] == []

    heaters = devices[drive.name]
    heater1 = heaters["signals"][0]
    assert heater1["access"] == "w" and heater1["limits"] == [0.0, 2500.0]
    assert heaters["commands"] == [
        {
            "name": "set_duty",
            "description": "Drive the elements at a fixed duty.",
            "simulation": False,
        },
        {"name": "off", "description": "Stop heating.", "simulation": True},
    ]
    assert heaters["state"] == {"conditions": [], "duty": 0.0}

    # After a delivery and a demand, the values ride along on the tree.
    sample = deliver(rig, daq, 5_000_000_000)
    rig.demand(drive.root, {"heater1": 3000.0})
    one = client.get(f"/api/devices/{daq.name}").json()
    assert one["signals"][0]["latest"] == {"time_ns": sample.time_ns, "value": 21.5}
    assert one["signals"][2]["latest"] == {"time_ns": sample.time_ns, "value": 0.0}, "RW reads too"
    heater1 = client.get(f"/api/devices/{drive.name}").json()["signals"][0]
    assert heater1["write"] == {
        "value": 2500.0,
        "requested": 3000.0,
        "at_limit": "high",
        "controller": None,
    }
    assert client.get("/api/devices/nope").status_code == 404


def test_devices_with_namespaces_nest_and_read_as_samples(client, rig, fresh):
    sensors = Sensors(fresh("hum"))
    rig.add_device(sensors)
    tree = client.get(f"/api/devices/{sensors.name}").json()["signals"]
    assert [n["name"] for n in tree] == ["chamber", "dry"]
    assert tree[1]["address"] == f"{sensors.name}.dry" and tree[1]["atomic"] is True
    assert [s["address"] for s in tree[1]["signals"]] == [
        f"{sensors.name}.dry.humidity",
        f"{sensors.name}.dry.temperature",
    ]
    assert client.get(f"/api/read/{sensors.name}.dry").status_code == 503, "nothing read yet"
    read = client.get(f"/api/read/{sensors.name}.dry?fresh=true").json()
    assert read == {
        "sample": {
            "node": f"{sensors.name}.dry",
            "time_ns": rig.clock.now_ns(),
            "values": {"humidity": 45.0, "temperature": 21.9},
        }
    }
    whole = client.get(f"/api/read/{sensors.name}?fresh=true").json()
    assert [s["node"] for s in whole["samples"]] == [
        f"{sensors.name}.chamber",
        f"{sensors.name}.dry",
    ]


def test_device_schema_and_commands(client, rig, drive, daq):
    schema = client.get(f"/api/devices/{drive.name}/schema").json()
    assert schema["type"] == "Drive" and schema["driver"] is None
    assert (
        schema["description"]
        == "Two heaters `[W]` with limits, one command; keeps what it was told to put out."
    )
    assert set(schema["commands"]) == {"set_duty", "off"}
    assert schema["commands"]["set_duty"]["arguments"]["properties"]["duty"]["type"] == "number"
    assert schema["commands"]["off"]["simulation"] is True
    assert schema["signals"]["heater1"] == {
        "address": f"{drive.name}.heater1",
        "access": "w",
        "label": "Heater 1",
        "quantity": "power",
        "unit": "W",
        "dimension": "Power",
        "range": None,
        "precision": None,
        "limits": [0.0, 2500.0],
    }
    everything = client.get("/api/schema").json()
    assert set(everything["devices"]) == {daq.name, drive.name}

    r = client.post(f"/api/devices/{drive.name}/commands/set_duty", json={"duty": 0.4})
    assert r.status_code == 200 and r.json()["duty"] == 0.4
    assert client.post(f"/api/devices/{drive.name}/commands/off").status_code == 200
    assert client.get(f"/api/devices/{drive.name}").json()["state"]["duty"] == 0.0
    assert (
        client.post(f"/api/devices/{drive.name}/commands/set_duty", json={"duty": "x"}).status_code
        == 422
    )
    assert (
        client.post(f"/api/devices/{drive.name}/commands/set_duty", json={"bogus": 1}).status_code
        == 422
    )
    assert client.post(f"/api/devices/{drive.name}/commands/explode").status_code == 404
    assert client.post("/api/devices/nope/commands/off").status_code == 404


# endregion

# region Reading and writing by address


def test_read_by_address(client, rig, daq, clock):
    zone1 = f"{daq.name}.zone1"
    assert client.get(f"/api/read/{zone1}").status_code == 503, "nothing has been read yet"
    assert client.get("/api/read/nowhere.zone1").status_code == 404
    assert client.get(f"/api/read/{daq.name}.zone9").status_code == 404
    sample = deliver(rig, daq, 5_000_000_000)
    assert client.get(f"/api/read/{zone1}").json() == {
        "reading": {"signal": zone1, "time_ns": 5_000_000_000, "value": 21.5}
    }
    assert daq.reads == 1, "a plain read is what is known; the device was not asked"
    assert client.get(f"/api/read/{daq.name}").json() == {
        "samples": [
            {
                "node": daq.name,
                "time_ns": sample.time_ns,
                "values": {"zone1": 21.5, "zone2": 22.0, "setpoint": 0.0},
            }
        ]
    }

    daq.temps["zone1"] = 99.0
    clock.advance(1.0)
    fresh = client.get(f"/api/read/{zone1}?fresh=true").json()
    assert fresh == {"reading": {"signal": zone1, "time_ns": clock.now_ns(), "value": 99.0}}
    assert daq.reads == 2 and rig.latest[daq.signals["zone1"]].value == 99.0, "delivered too"

    many = client.get(f"/api/read?at={zone1},{daq.name}.zone2,{daq.name}").json()
    assert [next(iter(r)) for r in many] == ["reading", "reading", "samples"]
    assert many[1]["reading"]["value"] == 22.0
    daq.temps["zone2"] = 50.0
    clock.advance(1.0)
    many = client.get(f"/api/read?at={zone1},{daq.name}.zone2&fresh=true").json()
    assert [r["reading"]["value"] for r in many] == [99.0, 50.0]
    assert daq.reads == 3, "one device read for both"
    assert client.get(f"/api/read?at={zone1},nowhere").status_code == 404


def test_demand_on_a_device_and_on_a_signal(client, rig, daq, drive):
    r = client.put(f"/api/devices/{drive.name}/demand", json={"heater1": 3000.0, "heater2": 10.0})
    assert r.status_code == 200
    assert r.json() == {
        f"{drive.name}.heater1": {
            "value": 2500.0,
            "requested": 3000.0,
            "at_limit": "high",
            "controller": None,
        },
        f"{drive.name}.heater2": {
            "value": 10.0,
            "requested": None,
            "at_limit": None,
            "controller": None,
        },
    }
    assert drive.inputs == {"heater1": 2500.0, "heater2": 10.0}

    r = client.put(f"/api/signals/{drive.name}.heater1", json=100)
    assert r.status_code == 200 and r.json()[f"{drive.name}.heater1"]["value"] == 100.0
    assert drive.inputs["heater1"] == 100.0

    # What the rig refuses, the route refuses with its message.
    r = client.put(f"/api/devices/{daq.name}/demand", json={"zone1": 1.0})
    assert r.status_code == 409 and r.json() == {
        "detail": f"'{daq.name}.zone1' [rp] is not writable"
    }
    assert client.put(f"/api/devices/{drive.name}/demand", json={"heater9": 1.0}).status_code == 404
    assert client.put(f"/api/devices/{drive.name}/demand", json={}).status_code == 422
    assert client.put("/api/devices/nope/demand", json={"x": 1.0}).status_code == 404
    r = client.put(f"/api/signals/{drive.name}", json=1.0)
    assert r.status_code == 409 and "is a namespace" in r.json()["detail"]
    assert client.put(f"/api/signals/{drive.name}.heater9", json=1.0).status_code == 404
    assert client.put(f"/api/signals/{drive.name}.heater1", json="x").status_code == 422

    # A setting is written the same way, and a fresh read sees it.
    client.put(f"/api/devices/{daq.name}/demand", json={"setpoint": 60.0})
    read = client.get(f"/api/read/{daq.name}.setpoint?fresh=true").json()
    assert read["reading"]["value"] == 60.0


# endregion

# region Waits, clock, health, events


def test_waits_can_be_listed_fired_and_interrupted(client, rig):
    lid = Trigger()
    rig.triggers.register("lid", lid, "Close the lid", prompt=True)
    assert client.get("/api/waits").json()["lid"]["outcome"] == "pending"
    assert client.get("/api/waits/lid").json()["prompt"] is True
    assert client.post("/api/waits/lid/fire").json() == {"name": "lid", "fired": True}
    assert lid.fired
    assert client.post("/api/waits/nope/fire").status_code == 404
    other = Trigger()
    rig.triggers.register("other", other)
    assert client.post("/api/waits/other/interrupt").json()["interrupted"] is True
    assert other.interrupted


def test_clock_reports_the_rig_s_timebase(client, rig, clock):
    clock.advance(2.5)
    rig.clock.tag("run")
    clock.advance(0.5)
    body = client.get("/api/clock").json()
    assert body["start_time_ns"] == rig.clock.start_time_ns
    assert body["now_ns"] == rig.clock.now_ns()
    assert body["elapsed_ns"] >= 0 and "run" in body["tags"]


def test_health_is_one_look_at_the_rig(client, rig, daq):
    body = client.get("/api/health").json()
    assert body["ok"] is True and body["recording"] is False
    assert body["devices"] == {} and body["controllers"] == {} and body["conditions"] == []
    assert body["alarms"] == {"warn": 0, "alarm": 0, "max_level": 0}
    assert body["waits"] == []
    daq.poll_s = 0.5
    rig.start_polling(daq)
    try:
        body = client.get("/api/health").json()
        assert body["devices"] == {daq.name: {"running": True, "last_read_ns": None}}
    finally:
        rig.polling.stop_all()


def test_health_counts_signals_outside_their_warn_and_alarm_bands(client, rig, daq):
    zone1, zone2 = daq.signals["zone1"], daq.signals["zone2"]
    rig.on_samples([Sample(daq.root, 1, {zone1: 1160.0, zone2: 1120.0})])
    body = client.get("/api/health").json()
    assert body["alarms"] == {"warn": 1, "alarm": 1, "max_level": 40}


def test_health_alarms_include_device_conditions_at_or_above_warning(client, rig, daq):
    daq.poll_s = 0.5
    daq.broken = True
    rig.start_polling(daq)
    rig.polling.stop_all()
    rig.polling._read(daq)  # one poll, as the loop would: it fails and stops
    body = client.get("/api/health").json()
    assert body["ok"] is False
    assert (
        body["conditions"][0]["device"] == daq.name and body["conditions"][0]["kind"] == "offline"
    )
    assert body["alarms"] == {"warn": 0, "alarm": 1, "max_level": 40}


def test_health_without_a_rig_says_so():
    set_rig(None)
    with TestClient(create_app()) as c:
        assert c.get("/api/health").json() == {"ok": False, "rig": None}


def test_events_are_kept_and_streamed(client, rig):
    rig.event(Level.WARNING, "device", "probe", "slow", "took 2 s", {"took_s": 2.0})
    rig.event(Level.INFO, "rig", "x", "note", "quiet")
    events = client.get("/api/events").json()
    assert [e["level"] for e in events] == ["WARNING", "INFO"]
    assert events[0]["details"] == {"took_s": 2.0} and events[0]["time_ns"] == rig.clock.now_ns()
    assert [e["kind"] for e in client.get("/api/events?level=warning").json()] == ["slow"]
    assert len(client.get("/api/events?limit=1").json()) == 1
    with client.websocket_connect("/ws/events") as ws:
        assert [e["kind"] for e in ws.receive_json()["events"]] == ["slow", "note"]
        rig.event(Level.ERROR, "program", "bake[2]", "step_failed", "no such device")
        (event,) = ws.receive_json()["events"]
        assert event["level"] == "ERROR" and event["subject"] == "bake[2]"


# endregion

# region Streams


def test_samples_stream_carries_only_what_publishes(client, rig, daq, clock):
    zone1, setting = daq.signals["zone1"], daq.signals["setpoint"]
    rig.on_samples([Sample(daq.root, 1, {zone1: 21.5, setting: 3.0})])
    with client.websocket_connect("/ws/samples") as ws:
        first = ws.receive_json()
        assert first == {"samples": [{"node": daq.name, "time_ns": 1, "values": {"zone1": 21.5}}]}
        rig.on_samples([Sample(daq.root, 2, {zone1: 22.0, setting: 4.0})])
        assert ws.receive_json() == {
            "samples": [{"node": daq.name, "time_ns": 2, "values": {"zone1": 22.0}}]
        }
        rig.on_samples([Sample(daq.root, 3, {setting: 5.0})])
        rig.on_samples([Sample(daq.root, 4, {zone1: 23.0})])
        frame = ws.receive_json()
        assert frame["samples"][0]["time_ns"] == 4, "a sample with nothing published is not sent"


def test_writes_stream_sends_a_snapshot_then_changes(client, rig, drive):
    rig.demand(drive.root, {"heater1": 10.0})
    with client.websocket_connect("/ws/writes") as ws:
        first = ws.receive_json()
        assert first == {
            "writes": [
                {
                    "signal": f"{drive.name}.heater1",
                    "value": 10.0,
                    "requested": None,
                    "at_limit": None,
                    "controller": None,
                }
            ]
        }
        rig.demand(drive.root, {"heater2": 9000.0})
        (state,) = ws.receive_json()["writes"]
        assert state["signal"] == f"{drive.name}.heater2" and state["at_limit"] == "high"


def test_devices_stream_sends_each_run(client, rig, daq):
    daq.poll_s = 0.5
    rig.start_polling(daq)
    rig.polling.stop_all()
    with client.websocket_connect("/ws/devices") as ws:
        (first,) = ws.receive_json()["devices"]
        assert first["name"] == daq.name and first["running"] is False
        assert first["period_s"] == 0.5 and first["conditions"] == []
        daq.broken = True
        rig.polling._read(daq)
        (run,) = ws.receive_json()["devices"]
        assert run["conditions"][0]["kind"] == "offline"


def test_waits_stream(client, rig):
    with client.websocket_connect("/ws/waits") as ws:
        rig.triggers.register("lid", Trigger(), "Close the lid", prompt=True)
        (state,) = ws.receive_json()["waits"]
        assert state["name"] == "lid" and state["outcome"] == "pending"


# endregion

# region Programs


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


def test_program_runs_step_by_step_as_waits_are_answered(client, programmer, rig):
    body = {"name": "go", "steps": [{"wait": "one"}, {"wait": {"message": "two", "name": "two"}}]}
    state = client.post("/api/programs/run", json=body).json()
    assert state == {
        "running": True,
        "step": 0,
        "steps": 2,
        "command": "wait",
        "failed": False,
        "error": None,
    }
    assert list(client.get("/api/waits").json()) == ["wait"]
    assert client.post("/api/waits/wait/fire").json()["fired"] is True
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and "two" not in rig.triggers.states():
        time.sleep(0.01)
    assert client.get("/api/programs/running").json()["step"] == 1
    assert client.post("/api/programs/interrupt").json()["running"] is False
    programmer.join(2)
    assert rig.triggers.states() == {}


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
    assert client.get("/api/waits").json() == {}


def test_a_step_naming_a_missing_controller_fails_the_run_instead_of_finishing_it(
    client, programmer
):
    """The bug this guards: the route returned the 404 but the run still narrated `finished`."""
    body = {"steps": [{"regulate": {"loop": "heaters.heater1", "setpoint": 20}}]}
    r = client.post("/api/programs/run", json=body)
    assert r.status_code == 404 and r.json() == {"detail": "Controller 'heaters.heater1' not found"}

    state = client.get("/api/programs/running").json()
    assert state["running"] is False and state["failed"] is True
    assert state["error"] is not None and "heaters.heater1" in state["error"]

    events = client.get("/api/events").json()
    kinds = [e["kind"] for e in events]
    assert kinds[-1] == "failed" and "finished" not in kinds
    (finish_event,) = [e for e in events if e["kind"] == "failed"]
    assert finish_event["level"] == "ERROR" and "heaters.heater1" in finish_event["message"]


# endregion

# region Simulation


class TestSimRoutes:
    @pytest.fixture
    def sim(self, client, tmp_path):
        from flyball.runtime.config import RigConfig
        from flyball.runtime.simulation import Simulation
        from flyball.server import set_simulation

        document = {
            "name": "tank",
            "clock": {"stepped": True},
            "links": {"tank": {"tag": "sim_plant", "model": "lag", "gain": 1.0, "tau_s": 10.0}},
            "devices": {
                "level": {
                    "driver": "sim_daq",
                    "poll_s": 1.0,
                    "link": "tank",
                    "ports": {"level": {"port": "output", "quantity": "level", "unit": "m"}},
                },
                "valve": {"driver": "sim_drive", "link": "tank", "ports": {"open": "input"}},
            },
        }
        config = RigConfig.model_validate(document)
        rig = config.build(start=False)
        simulation = Simulation(rig, config, document, tmp_path / "tank.yaml")
        set_rig(rig)
        set_simulation(simulation)
        yield simulation
        set_simulation(None)
        set_rig(None)

    def test_a_hardware_rig_says_it_is_not_simulated(self, client):
        assert client.get("/api/sim").json() == {"simulated": False}
        assert client.put("/api/sim/clock", json={"speed": 2}).status_code == 409

    def test_clock_plants_and_save(self, client, sim, tmp_path):
        state = client.get("/api/sim").json()
        assert state["simulated"] is True and list(state["plants"]) == ["tank"]
        assert state["clock"]["stepped"] is True
        assert client.post("/api/sim/clock/step", json={"seconds": 2}).json()["now_ns"] > 0
        assert client.put("/api/sim/clock", json={"speed": 2}).status_code == 409, "stepped"
        assert client.put("/api/sim/plants/tank", json={"gain": 2.5}).json()["gain"] == 2.5
        assert client.put("/api/sim/plants/ghost", json={}).status_code == 404
        reset = client.post("/api/sim/plants/tank/reset", json={"output": 12.0}).json()
        assert reset["output"] == pytest.approx(12.0, abs=0.5)
        assert client.get("/api/sim/config").json()["links"]["tank"]["gain"] == 2.5
        saved = client.post("/api/sim/save").json()["path"]
        assert saved == str(tmp_path / "tank.yaml") and (tmp_path / "tank.yaml").exists()
        assert client.get("/api/sim").json()["changed"] == []
        devices = {d["name"]: d for d in client.get("/api/devices").json()}
        assert devices["level"]["driver"] == "sim_daq" and devices["level"]["link"] == "tank"
        assert devices["valve"]["signals"][0]["access"] == "w"


# endregion


def test_a_command_on_an_offline_device_restarts_it(client, rig, daq):
    daq.poll_s = 0.5
    daq.broken = True
    rig.start_polling(daq)
    try:
        rig.polling.stop_all()
        rig.polling._read(daq)  # one poll, as the loop would: it fails and stops
        assert rig.polling.run(daq.name).running is False
        body = client.get(f"/api/devices/{daq.name}").json()
        assert body["run"] == {"period_s": 0.5, "running": False, "last_read_ns": None}
        assert body["conditions"][0]["kind"] == "offline"

        assert client.post(f"/api/devices/{daq.name}/commands/restore").status_code == 200
        assert rig.polling.run(daq.name).running is True
        assert rig.polling.run(daq.name).conditions == ()
        rig.polling.stop_all()

        restarted = client.post(f"/api/devices/{daq.name}/restart").json()
        assert restarted["run"]["running"] is True and restarted["conditions"] == []
        assert client.post("/api/devices/nope/restart").status_code == 404
    finally:
        rig.polling.stop_all()
