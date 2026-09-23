"""Downloads: a session as csv/json/zip, one signal, one controller, one write, the events."""

from __future__ import annotations

import csv
import io
import json
import zipfile

import pytest
from flyball_sim.clock import SteppedClock

from conftest import TestClient
from flyball.control import PI
from flyball.foundation.device import Access, Device, Role, Sample, SignalSpec, WriteState
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Watt
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.deps import set_store
from flyball.model.controller import Controller
from flyball.model.feedforward import NoFeedforward
from flyball.record.sqlite import SqliteStore
from flyball.record.types import Event, Tick
from flyball.rig import Rig

START_NS = 1_700_000_000_000_000_000


class Probe(Device):
    TREE = (
        SignalSpec(name="temperature", quantity=Quantity("temperature", Celsius), access=Access.RP),
    )


class Meter(Device):
    TREE = (SignalSpec(name="power", quantity=Quantity("power", Watt), access=Access.RP),)


class Heater(Device):
    TREE = (
        SignalSpec(
            name="power",
            quantity=Quantity("power", Watt),
            access=Access.RPW,
            role=Role.DEMAND,
            limits=(0.0, 10.0),
        ),
    )


@pytest.fixture
def client(tmp_path, fresh):
    """A store with one closed session: two devices read, one written, one controller, one event."""
    probe, meter, heater = Probe(fresh("probe")), Meter(fresh("meter")), Heater(fresh("heater"))
    temperature, power, drive = (
        probe.signals["temperature"],
        meter.signals["power"],
        heater.signals["power"],
    )
    controller = Controller(
        SteppedClock(0), drive, temperature, law=PI(kp=1.0), feedforward=NoFeedforward()
    )
    store = SqliteStore(tmp_path / "t.db")
    writer = store.open_session(start_ns=START_NS, config={"name": "t"})
    for device in (probe, meter, heater):
        writer.declare_device(device)
    for signal in (temperature, power, drive):
        writer.declare_signal(signal)
    writer.declare_controller(controller)
    writer.write_samples([
        Sample(probe.root, START_NS + 1_000_000_000, {temperature: 20.5}),
        Sample(meter.root, START_NS + 1_000_000_000, {power: 100.0}),
        Sample(probe.root, START_NS + 2_000_000_000, {temperature: 21.0}),
    ])
    writer.write_tick(
        Tick(
            controller.name,
            2_000_000_000,
            "regulating",
            5.0,
            measured=21.0,
            setpoint=25.0,
            output=5.0,
        )
    )
    writer.write_states(
        2_000_000_000,
        {drive: WriteState(value=5.0, requested=None, at_limit=None, controller=controller.name)},
    )
    writer.write_event(Event(1_500_000_000, "note", detail={"x": 1}))
    session_id = writer.session.id
    writer.end(START_NS + 3_000_000_000)
    rig = Rig()
    set_rig(rig)
    set_store(store)
    with TestClient(create_app()) as c:
        yield c, session_id, probe.name, meter.name, heater.name
    set_rig(None)
    set_store(None)


def rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def test_session_wide_aligns_devices_on_one_time_axis(client):
    c, sid, probe, meter, heater = client
    r = c.get(f"/api/history/sessions/{sid}/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == f'attachment; filename="session-{sid}-wide.csv"'
    table = rows(r.text)
    assert table[0] == [
        "time_s",
        "time",
        f"{probe}.temperature (°C)",
        f"{meter}.power (W)",
        f"{heater}.power (W)",
    ]
    assert table[1] == ["1.0", "2023-11-14T22:13:21.000Z", "20.5", "100.0", ""]
    assert table[2] == ["2.0", "2023-11-14T22:13:22.000Z", "21.0", "100.0", ""]  # the meter's held
    grid = rows(c.get(f"/api/history/sessions/{sid}/export?step_s=0.5").text)
    assert [r[0] for r in grid[1:]] == ["0.0", "0.5", "1.0", "1.5", "2.0"]
    assert grid[1][2:] == ["", "", ""] and grid[4][2:4] == ["20.5", "100.0"]


def test_session_long_json_and_zip(client):
    c, sid, probe, meter, heater = client
    long = rows(c.get(f"/api/history/sessions/{sid}/export?layout=long").text)
    assert long[0] == ["time_s", "time", "device", "signal", "unit", "value"]
    assert len(long) == 4 and long[1][2:] == [probe, f"{probe}.temperature", "°C", "20.5"]

    as_json = c.get(f"/api/history/sessions/{sid}/export?format=json").json()
    assert as_json[0]["time_s"] == 1.0 and as_json[1][f"{probe}.temperature (°C)"] == 21.0

    r = c.get(f"/api/history/sessions/{sid}/export?format=zip")
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        assert set(archive.namelist()) == {
            "signals-wide.csv",
            "signals-long.csv",
            "events.csv",
            f"controller-{heater}.power.csv",
            f"write-{heater}.power.csv",
            "session.json",
        }
        meta = json.loads(archive.read("session.json"))
        assert meta["id"] == sid and meta["controllers"] == [
            {
                "name": f"{heater}.power",
                "measured": f"{probe}.temperature",
                "law": {"tag": "PI", "kp": 1.0, "ki": 0.0, "tt": 0.0, "b": 1.0},
                "feedforward": {"tag": "none"},
            }
        ]
        assert meta["devices"][0] == {
            "address": probe,
            "driver": "Probe",
            "config": {"link": None},
            "label": None,
        }
        assert [s["address"] for s in meta["signals"]] == [
            f"{probe}.temperature",
            f"{meter}.power",
            f"{heater}.power",
        ]
        assert meta["start"] == "2023-11-14T22:13:20.000Z" and meta["end"] is not None
        write = rows(archive.read(f"write-{heater}.power.csv").decode())
        assert write[0] == ["time_s", "time", "value", "requested", "at_limit", "controller"]
        assert write[1] == ["2.0", "2023-11-14T22:13:22.000Z", "5.0", "", "", f"{heater}.power"]


def test_signal_controller_write_and_events(client):
    c, sid, probe, meter, heater = client
    series = rows(c.get(f"/api/history/sessions/{sid}/series/{probe}.temperature/export").text)
    assert series[0][2] == f"{probe}.temperature (°C)" and series[1][2] == "20.5"

    ticks = rows(c.get(f"/api/history/sessions/{sid}/ticks/{heater}.power/export").text)
    assert ticks[0][2:5] == ["mode", "setpoint", "measured"]
    assert ticks[1][:5] == ["2.0", "2023-11-14T22:13:22.000Z", "regulating", "25.0", "21.0"]

    writes = c.get(f"/api/history/sessions/{sid}/writes/{heater}.power/export?format=json").json()
    assert writes == [
        {
            "time_s": 2.0,
            "time": "2023-11-14T22:13:22.000Z",
            "value": 5.0,
            "requested": None,
            "at_limit": None,
            "controller": f"{heater}.power",
        }
    ]

    events = c.get(f"/api/history/sessions/{sid}/events/export?format=json").json()
    assert events == [
        {
            "time_s": 1.5,
            "time": "2023-11-14T22:13:21.500Z",
            "kind": "note",
            "source": "",
            "detail": '{"x": 1}',
        }
    ]

    assert c.get(f"/api/history/sessions/{sid + 1}/export").status_code == 404
    assert c.get(f"/api/history/sessions/{sid}/series/{probe}.nothing/export").status_code == 409


def test_history_routes_read_by_address(client):
    c, sid, probe, meter, heater = client
    devices = c.get(f"/api/history/sessions/{sid}/devices").json()
    assert [d["address"] for d in devices] == [probe, meter, heater]
    signals = c.get(f"/api/history/sessions/{sid}/signals").json()
    assert signals[0] == {
        "id": 1,
        "device_id": 1,
        "address": f"{probe}.temperature",
        "quantity": "temperature",
        "unit": "°C",
        "access": "rp",
        "dtype": "float",
        "shape": [],
        "label": None,
        "range": None,
        "precision": None,
        "warning": None,
        "alarm": None,
        "limits": None,
    }
    (write,) = c.get(f"/api/history/sessions/{sid}/writes").json()
    assert write["signal"]["address"] == f"{heater}.power" and write["limits"] == [0.0, 10.0]
    (controller,) = c.get(f"/api/history/sessions/{sid}/controllers").json()
    assert (
        controller["name"] == f"{heater}.power" and controller["measured"] == f"{probe}.temperature"
    )

    series = c.get(f"/api/history/sessions/{sid}/series/{probe}.temperature").json()
    assert series["signal"]["address"] == f"{probe}.temperature"
    assert series["points"] == [
        {"offset_ns": 1_000_000_000, "value": 20.5},
        {"offset_ns": 2_000_000_000, "value": 21.0},
    ]
    bucketed = c.get(
        f"/api/history/sessions/{sid}/series/{probe}.temperature?max_points=2&start_ns=1500000000"
    ).json()
    assert [p["value"] for p in bucketed["points"]] == [21.0]

    states = c.get(f"/api/history/sessions/{sid}/writes/{heater}.power").json()
    assert states == [
        {
            "offset_ns": 2_000_000_000,
            "value": 5.0,
            "requested": None,
            "at_limit": None,
            "controller": f"{heater}.power",
        }
    ]
    ticks = c.get(f"/api/history/sessions/{sid}/ticks/{heater}.power").json()
    assert len(ticks) == 1 and ticks[0]["controller"] == f"{heater}.power"
    assert c.get(f"/api/history/sessions/{sid}/series/{probe}.nothing").status_code == 404, (
        "the session never declared it"
    )
