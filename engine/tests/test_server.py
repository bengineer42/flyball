"""The HTTP and websocket surface, through FastAPI's test client."""

from __future__ import annotations

import time
from collections.abc import Iterator
from enum import Enum

import pytest
from fastapi.testclient import TestClient

from flyball.foundation.device import (
    Committable,
    Demand,
    Level,
    Namespace,
    Node,
    Output,
    Readable,
    Sample,
    Signal,
    command,
)
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Percent, Watt
from flyball.foundation.router import Trigger
from flyball.interfaces.server import create_app, set_rig

TEMP = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)
HUMIDITY = Quantity("humidity", Percent)

# region Test drivers: a DAQ (RP zones and an RPW demand) and a drive (demands with limits), the
# furnace shape from the plan's §2 example, and a sensor set with atomic namespaces.


class Daq(Readable, Committable):
    """Two thermocouple zones `[RP]` and a demand `[RPW]`; counts the reads asked of it."""

    zone1 = Output(
        "zone1",
        "Zone 1",
        TEMP,
        range=(0.0, 1200.0),
        precision=1,
        warn=(0.0, 1100.0),
        alarm=(-10.0, 1150.0),
    )
    zone2 = Output("zone2", "", TEMP, warn=(0.0, 1100.0))
    setpoint = Demand("setpoint", "", TEMP)

    def __init__(self, name: str, label: str | None = None) -> None:
        super().__init__(name, label)
        self.temps = {"zone1": 21.5, "zone2": 22.0, "setpoint": 0.0}
        self.reads = 0
        self.broken = False

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        self.reads += 1
        if self.broken:
            raise OSError("open circuit")
        yield Sample(self.root, time_ns, {self.signals[n]: v for n, v in self.temps.items()})

    def commit(self, time_ns: int) -> None:
        for signal, value in self.pending.items():
            self.temps[signal.name] = value

    @command
    def restore(self) -> None:
        """Mend it."""
        self.broken = False


class Drive(Committable):
    """Two heater demands with limits, and a duty command that pushes an output."""

    heater1 = Demand("heater1", "Heater 1", POWER, limits=(0.0, 2500.0))
    heater2 = Demand("heater2", "", POWER, limits=(0.0, 6000.0))
    duty = Output("duty", "Duty", initial=0.0)

    def __init__(self, name: str, label: str | None = None) -> None:
        super().__init__(name, label)
        self.inputs: dict[str, float] = {}

    def write_signal(self, signal: Signal, value: float) -> None:
        self.inputs[signal.name] = value

    @command
    def set_duty(self, duty: float, ramp_s: float = 0.0) -> float:
        """Drive the elements at a fixed duty."""
        self.duty.push(duty)
        return duty

    @command(tag="off", simulation=True)
    def switch_off(self) -> None:
        """Stop heating."""
        self.duty.push(0.0)

    @command(simulation=True)
    def kick(self, signal: str, offset: float) -> None:
        """Nudge one heater's output by `offset` W."""


class Sensors(Readable):
    """Two atomic namespaces, each read in its own transaction."""

    chamber = Namespace("chamber", atomic=True)
    dry = Namespace("dry", atomic=True)
    chamber_humidity = chamber.output("humidity", "", HUMIDITY)
    chamber_temperature = chamber.output("temperature", "", TEMP)
    dry_humidity = dry.output("humidity", "", HUMIDITY)
    dry_temperature = dry.output("temperature", "", TEMP)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        nodes = self.root.descendants() if node is None or node is self.root else (node,)
        for n in nodes:
            yield Sample(n, time_ns, {n.signals["humidity"]: 45.0, n.signals["temperature"]: 21.9})


class Mode(Enum):
    IDLE = "idle"
    RUNNING = "running"


class Typed(Readable):
    """One enum-valued output and one JSON-valued output, both `[RP]`."""

    mode = Output("mode", "", TEMP, vtype=Mode)
    config = Output("config", "", TEMP, vtype=dict)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        yield self.sample(time_ns, mode=Mode.RUNNING, config={"gain": 2, "offset": 1})


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
def typed(rig, fresh) -> Typed:
    typed = Typed(fresh("typed"))
    rig.add_device(typed)
    return typed


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
    assert [s["name"] for s in furnace["signals"]] == [
        "conditions",
        "zone1",
        "zone2",
        "setpoint",
        "last",
    ]
    zone1 = furnace["signals"][1]
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
        "role": "output",
        "tags": {},
        "initial": None,
        "latest": None,
        "write": None,
    }
    assert furnace["signals"][3]["access"] == "rpw", "a demand is readable, publishing and writable"
    assert furnace["commands"] == [
        {
            "name": "restore",
            "description": "Mend it.",
            "simulation": False,
            "commit": False,
            "mode": None,
            "interrupts": False,
            "demand_of": None,
            "links": {},
        },
        {
            "name": "set_setpoint",
            "description": "Set setpoint.",
            "simulation": False,
            "commit": False,
            "mode": None,
            "interrupts": False,
            "demand_of": "setpoint",
            "links": {},
        },
    ]
    assert furnace["conditions"] == []

    heaters = devices[drive.name]
    assert [s["name"] for s in heaters["signals"]] == [
        "conditions",
        "heater1",
        "heater2",
        "duty",
        "last",
    ]
    heater1 = heaters["signals"][1]
    assert heater1["access"] == "rpw" and heater1["limits"] == [0.0, 2500.0]
    assert heaters["commands"] == [
        {
            "name": "set_duty",
            "description": "Drive the elements at a fixed duty.",
            "simulation": False,
            "commit": False,
            "mode": None,
            "interrupts": False,
            "demand_of": None,
            "links": {},
        },
        {
            "name": "off",
            "description": "Stop heating.",
            "simulation": True,
            "commit": False,
            "mode": None,
            "interrupts": False,
            "demand_of": None,
            "links": {},
        },
        {
            "name": "kick",
            "description": "Nudge one heater's output by `offset` W.",
            "simulation": True,
            "commit": False,
            "mode": None,
            "interrupts": False,
            "demand_of": None,
            "links": {},
        },
        {
            "name": "set_heater1",
            "description": "Set Heater 1.",
            "simulation": False,
            "commit": False,
            "mode": None,
            "interrupts": False,
            "demand_of": "heater1",
            "links": {},
        },
        {
            "name": "set_heater2",
            "description": "Set heater2.",
            "simulation": False,
            "commit": False,
            "mode": None,
            "interrupts": False,
            "demand_of": "heater2",
            "links": {},
        },
    ]

    # After a delivery and a demand, the values ride along on the tree.
    sample = deliver(rig, daq, 5_000_000_000)
    rig.demand(drive.root, {"heater1": 3000.0})
    one = client.get(f"/api/devices/{daq.name}").json()
    assert one["signals"][1]["latest"] == {"time_ns": sample.time_ns, "value": 21.5}
    assert one["signals"][3]["latest"] == {"time_ns": sample.time_ns, "value": 0.0}, "RW reads too"
    heater1 = client.get(f"/api/devices/{drive.name}").json()["signals"][1]
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
    assert [n["name"] for n in tree] == ["conditions", "chamber", "dry"]
    assert tree[2]["address"] == f"{sensors.name}.dry" and tree[2]["atomic"] is True
    assert [s["address"] for s in tree[2]["signals"]] == [
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
            "writes": {},
        }
    }
    whole = client.get(f"/api/read/{sensors.name}?fresh=true").json()
    assert [s["node"] for s in whole["samples"]] == [
        sensors.name,  # the base class's `conditions`, pushed once at build
        f"{sensors.name}.chamber",
        f"{sensors.name}.dry",
    ]


def test_device_schema_and_commands(client, rig, drive, daq):
    schema = client.get(f"/api/devices/{drive.name}/schema").json()
    assert schema["type"] == "Drive" and schema["driver"] is None
    assert (
        schema["description"]
        == "Two heater demands with limits, and a duty command that pushes an output."
    )
    assert set(schema["commands"]) == {"set_duty", "off", "kick", "set_heater1", "set_heater2"}
    assert schema["commands"]["set_duty"]["arguments"]["properties"]["duty"]["type"] == "number"
    assert schema["commands"]["kick"]["arguments"]["properties"]["signal"]["enum"] == list(
        drive.signals
    ), "an argument called `signal` offers the device's own signals"
    assert schema["commands"]["off"]["simulation"] is True
    assert schema["signals"]["heater1"] == {
        "address": f"{drive.name}.heater1",
        "access": "rpw",
        "role": "demand",
        "tags": {},
        "label": "Heater 1",
        "quantity": "power",
        "unit": "W",
        "dimension": "Power",
        "dtype": "float",
        "value": {"type": "number"},
        "range": [0.0, 2500.0],  # nothing of its own: its limits stand in for a gauge
        "precision": None,
        "limits": [0.0, 2500.0],
    }
    everything = client.get("/api/schema").json()
    assert set(everything["devices"]) == {daq.name, drive.name}

    r = client.post(f"/api/devices/{drive.name}/commands/set_duty", json={"duty": 0.4})
    assert r.status_code == 200 and r.json() == 0.4
    assert client.post(f"/api/devices/{drive.name}/commands/off").status_code == 200
    tree = client.get(f"/api/devices/{drive.name}").json()["signals"]
    duty = next(s for s in tree if s["name"] == "duty")
    assert duty["latest"]["value"] == 0.0
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
                "writes": {},
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

    # A demand is written the same way, and a fresh read sees it.
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


def test_samples_stream_carries_only_what_publishes(rig, fresh):
    """A config is `[R]`, not `[P]`: mixed into a sample with a zone, only the zone streams."""
    from flyball.foundation.device import ConfigSignal

    class Mixed(Readable):
        zone = Output("zone", "", TEMP)
        static = ConfigSignal("static", "", TEMP)

        def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
            yield self.sample(time_ns, zone=0.0)

    device = Mixed(fresh("mixed"))
    rig.add_device(device)
    zone, static = device.signals["zone"], device.signals["static"]
    set_rig(rig)
    with TestClient(create_app()) as client:
        rig.on_samples([Sample(device.root, 1, {zone: 21.5, static: 3.0})])
        with client.websocket_connect("/ws/samples") as ws:
            first = ws.receive_json()
            assert first == {
                "samples": [
                    {  # the snapshot: every publishing signal's newest, conditions included
                        "node": device.name,
                        "time_ns": 1,
                        "values": {"conditions": [], "zone": 21.5},
                        "writes": {},
                    }
                ]
            }
            rig.on_samples([Sample(device.root, 2, {zone: 22.0, static: 4.0})])
            assert ws.receive_json() == {
                "samples": [
                    {
                        "node": device.name,
                        "time_ns": 2,
                        "values": {"conditions": [], "zone": 22.0},
                        "writes": {},
                    }
                ]
            }
            rig.on_samples([Sample(device.root, 3, {static: 5.0})])
            rig.on_samples([Sample(device.root, 4, {zone: 23.0})])
            frame = ws.receive_json()
            assert frame["samples"][0]["time_ns"] == 4, "nothing published is not sent"
    set_rig(None)


def test_read_and_samples_stream_carry_enum_and_json_values(client, rig, typed):
    (sample,) = typed.read(rig.clock.now_ns())
    rig.on_samples([sample])

    mode = client.get(f"/api/read/{typed.name}.mode").json()
    assert mode == {
        "reading": {"signal": f"{typed.name}.mode", "time_ns": sample.time_ns, "value": "running"}
    }
    config = client.get(f"/api/read/{typed.name}.config").json()
    assert config["reading"]["value"] == {"gain": 2, "offset": 1}

    with client.websocket_connect("/ws/samples") as ws:
        first = ws.receive_json()
        entry = next(s for s in first["samples"] if s["node"] == typed.name)
        assert entry["values"] == {
            "conditions": [],
            "mode": "running",
            "config": {"gain": 2, "offset": 1},
        }


def _sample_of(frame: dict, node: str) -> dict:
    return next(s for s in frame["samples"] if s["node"] == node)


def test_writes_ride_the_samples_stream(client, rig, drive):
    """`/ws/writes` is gone: a demand's write record rides with its reading on `/ws/samples`."""
    rig.demand(drive.root, {"heater1": 10.0})
    with client.websocket_connect("/ws/samples") as ws:
        first = _sample_of(ws.receive_json(), drive.name)
        assert first["values"]["heater1"] == 10.0
        assert first["writes"] == {
            "heater1": {"requested": None, "at_limit": None, "controller": None}
        }
        rig.demand(drive.root, {"heater2": 9000.0})
        frame = _sample_of(ws.receive_json(), drive.name)
        assert frame["writes"]["heater2"]["at_limit"] == "high"


def test_device_runs_ride_the_samples_stream(client, rig, daq):
    """`/ws/devices` is gone: a device's run rides beside its samples on `/ws/samples`."""
    daq.poll_s = 0.5
    rig.start_polling(daq)
    rig.polling.stop_all()
    with client.websocket_connect("/ws/samples") as ws:
        (first,) = ws.receive_json()["runs"]
        assert first["name"] == daq.name and first["running"] is False
        assert first["period_s"] == 0.5 and first["conditions"] == []
        daq.broken = True
        rig.polling._read(daq)
        (run,) = ws.receive_json()["runs"]
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
    from flyball.interfaces.server import set_programmer
    from flyball.sequencing import Programmer

    programmer = Programmer(rig)
    set_programmer(programmer)
    yield programmer
    programmer.interrupt()
    set_programmer(None)


def test_load_tunings_stores_law_configs_under_the_directory_from_their_file_stem(tmp_path, rig):
    from flyball.interfaces.server.routes.library import load_tunings

    (tmp_path / "gentle.yaml").write_text("tag: P\nkp: 0.5\n")
    (tmp_path / "brisk.toml").write_text('tag = "PID"\nkp = 0.8\nki = 0.08\nkd = 1.0\ntt = 5\n')
    (tmp_path / "notes.txt").write_text("not a tuning")

    loaded = load_tunings(rig, tmp_path)
    assert sorted(loaded) == ["brisk", "gentle"]
    assert rig.tunings.get("gentle").kp == 0.5
    assert rig.tunings.get("brisk").kp == 0.8

    assert load_tunings(rig, tmp_path / "no_such_directory") == []


def test_program_check_warns_of_what_the_rig_lacks(client, programmer, drive):
    """A tuning, controller or device command the rig lacks is a warning per step, not a refusal."""
    body = {
        "steps": [
            {"regulate": {"setpoint": 30, "tuning": "brisk"}},  # no default controller, no tuning
            {"manual": "no_such_controller"},
            {"command": {"device_command": "nope", "device": drive.name}},
            {"command": {"device_command": "off", "device": "ghost"}},
            {"wait": "fine"},
        ]
    }
    checked = client.post("/api/programs/check", json=body).json()
    assert checked["ok"] is True
    assert checked["warnings"] == {
        "0": "the rig has no default controller; tuning 'brisk' is not stored",
        "1": "controller 'no_such_controller' is not on the rig",
        "2": f"{drive.name!r} has no command 'nope'",
        "3": "device 'ghost' is not on the rig",
    }


def test_program_check_normalises_without_running(client, programmer):
    body = {"name": "t", "steps": [{"wait": "press go"}, {"wait": {"message": "m", "name": "n"}}]}
    checked = client.post("/api/programs/check", json=body).json()
    assert checked["ok"] is True and checked["warnings"] == {}
    normalised = checked["normalised"]
    assert normalised["steps"][0] == {"command": {"command": "wait", "message": "press go"}}
    assert normalised["steps"][1]["command"]["name"] == "n"
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


def test_program_that_needs_no_waiting_finishes_at_once(client, programmer, rig, fresh):
    from dataclasses import dataclass

    from flyball.model.catalog import get_catalog
    from flyball.sequencing import Command

    seen = []
    tag = fresh("note")

    @dataclass(frozen=True)
    class Note(Command, tag=tag, primary="text"):
        """Append to a list."""

        text: str

        def run(self, rig, operator=None):
            seen.append(self.text)
            return None

    get_catalog().register_command(Note)

    state = client.post("/api/programs/run", json={"steps": [{tag: "a"}, {tag: "b"}]}).json()
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
        from flyball_sim.simulation import Simulation

        from flyball.interfaces.server import set_simulation
        from flyball.runtime.config import RigConfig

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
        r = client.post("/api/sim/save")
        assert r.status_code == 409 and "--allow-save" in r.json()["detail"]
        from conftest import FakeRunner
        from flyball.interfaces.server.deps import set_runner
        from flyball.runtime.config import RunnerConfig

        set_runner(FakeRunner(RunnerConfig(allow_save=True)))
        try:
            saved = client.post("/api/sim/save").json()["path"]
        finally:
            set_runner(None)
        assert saved == str(tmp_path / "tank.yaml") and (tmp_path / "tank.yaml").exists()
        assert client.get("/api/sim").json()["changed"] == []
        devices = {d["name"]: d for d in client.get("/api/devices").json()}
        assert devices["level"]["driver"] == "sim_daq" and devices["level"]["link"] == "tank"
        valve_open = next(s for s in devices["valve"]["signals"] if s["name"] == "open")
        assert valve_open["access"] == "rpw"


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
