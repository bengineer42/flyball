"""Downloads: a session as csv/json/zip, one channel, one loop, the events."""

from __future__ import annotations

import csv
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from flyball.core.reading import Measurand, Sample, Source
from flyball.core.units.si import Celsius, Watt
from flyball.db.sqlite import SqliteStore
from flyball.db.types import Event, Tick
from flyball.runtime.rig import Rig
from flyball.server import create_app, set_rig
from flyball.server.deps import set_store

START_NS = 1_700_000_000_000_000_000


@pytest.fixture
def client(tmp_path, fresh):
    """A store with one closed session: two sources, one loop, one event."""
    temperature = Measurand(fresh("temperature"), Celsius)
    power = Measurand(fresh("power"), Watt)
    probe = Source(fresh("probe"), [temperature])
    meter = Source(fresh("meter"), [power])
    store = SqliteStore(tmp_path / "t.db")
    writer = store.open_session(start_ns=START_NS, config={"name": "t"})
    writer.declare_source(probe)
    writer.declare_source(meter)
    writer.declare_actuator("heater", "Heater")
    writer.declare_loop("heater", probe[temperature], {"tag": "PI", "kp": 1.0}, {"tag": "none"})
    writer.write_samples([
        Sample(probe, 1, START_NS + 1_000_000_000, {temperature: 20.5}),
        Sample(meter, 1, START_NS + 1_000_000_000, {power: 100.0}),
        Sample(probe, 2, START_NS + 2_000_000_000, {temperature: 21.0}),
    ])
    writer.write_tick(
        Tick("heater", 2_000_000_000, "regulating", 5.0, reading=21.0, setpoint=25.0, demand=5.0)
    )
    writer.write_event(Event(1_500_000_000, "note", detail={"x": 1}))
    session_id = writer.session.id
    writer.end(START_NS + 3_000_000_000)
    rig = Rig()
    set_rig(rig)
    set_store(store)
    with TestClient(create_app()) as c:
        yield c, session_id, probe.name, meter.name, temperature.name, power.name
    set_rig(None)
    set_store(None)
    Source.forget(probe.name)
    Source.forget(meter.name)


def rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def test_session_wide_aligns_sources_on_one_time_axis(client):
    c, sid, probe, meter, temperature, power = client
    r = c.get(f"/api/history/sessions/{sid}/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == f'attachment; filename="session-{sid}-wide.csv"'
    table = rows(r.text)
    assert table[0] == ["time_s", "time", f"{probe}.{temperature} (°C)", f"{meter}.{power} (W)"]
    assert table[1] == ["1.0", "2023-11-14T22:13:21.000Z", "20.5", "100.0"]
    assert table[2] == ["2.0", "2023-11-14T22:13:22.000Z", "21.0", "100.0"]  # the meter's held
    grid = rows(c.get(f"/api/history/sessions/{sid}/export?step_s=0.5").text)
    assert [r[0] for r in grid[1:]] == ["0.0", "0.5", "1.0", "1.5", "2.0"]
    assert grid[1][2:] == ["", ""] and grid[2][2:] == ["", ""] and grid[4][2:] == ["20.5", "100.0"]


def test_session_long_json_and_zip(client):
    c, sid, probe, meter, temperature, power = client
    long = rows(c.get(f"/api/history/sessions/{sid}/export?layout=long").text)
    assert long[0] == ["time_s", "time", "source", "measurand", "unit", "value"]
    assert len(long) == 4 and long[1][2:] == [probe, temperature, "°C", "20.5"]

    as_json = c.get(f"/api/history/sessions/{sid}/export?format=json").json()
    assert as_json[0]["time_s"] == 1.0 and as_json[1][f"{probe}.{temperature} (°C)"] == 21.0

    r = c.get(f"/api/history/sessions/{sid}/export?format=zip")
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        assert set(archive.namelist()) == {
            "channels-wide.csv",
            "channels-long.csv",
            "events.csv",
            "loop-heater.csv",
            "session.json",
        }
        meta = json.loads(archive.read("session.json"))
        assert meta["id"] == sid and meta["loops"][0]["feedforward"] == {"tag": "none"}
        assert meta["start"] == "2023-11-14T22:13:20.000Z" and meta["end"] is not None


def test_channel_loop_and_events(client):
    c, sid, probe, meter, temperature, power = client
    series = rows(c.get(f"/api/history/sessions/{sid}/series/{probe}/{temperature}/export").text)
    assert series[0][2] == f"{probe}.{temperature} (°C)" and series[1][2] == "20.5"

    ticks = rows(c.get(f"/api/history/sessions/{sid}/ticks/heater/export").text)
    assert ticks[0][2:5] == ["mode", "setpoint", "reading"]
    assert ticks[1][:5] == ["2.0", "2023-11-14T22:13:22.000Z", "regulating", "25.0", "21.0"]

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
