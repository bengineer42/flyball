"""Controllers over HTTP: schema, make, regulate, manual, reference, remove, and the stream."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flyball.control import Transfer
from flyball.control.laws import P
from flyball.server import create_app, set_rig
from test_server import Daq, Drive, deliver


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


def test_controller_lifecycle_over_http(client, rig, daq, drive, clock):
    target, source = f"{drive.name}.heater1", f"{daq.name}.zone1"
    deliver(rig, daq)
    assert client.get("/api/controllers").json() == []
    assert client.get("/api/controllers/default").status_code == 503

    schema = client.get("/api/controllers/schema").json()
    # Every signal publishes now (`conditions`, `last.*` included), so check the temperature
    # zones are among the sources, in order, rather than the whole (much longer) list.
    source_addresses = [s["address"] for s in schema["sources"]]
    assert source_addresses.index(source) < source_addresses.index(f"{daq.name}.zone2")
    zone1_choice = next(s for s in schema["sources"] if s["address"] == source)
    assert zone1_choice["dimension"] == "Temperature"
    assert [s["address"] for s in schema["targets"]] == [
        f"{daq.name}.setpoint",
        target,
        f"{drive.name}.heater2",
    ]
    assert schema["targets"][1]["limits"] == [0.0, 2500.0]
    tags = {d["properties"]["tag"]["const"] for d in schema["laws"]["$defs"].values()}
    assert "PI" in tags and schema["laws"]["discriminator"]["propertyName"] == "tag"
    ff = {d["properties"]["tag"]["const"] for d in schema["feedforwards"]["$defs"].values()}
    assert ff >= {"setpoint", "none", "affine", "table"}
    generators = {
        d["properties"]["tag"]["const"]
        for d in schema["generators"]["$defs"].values()
        if "tag" in d.get("properties", {})
    }
    assert generators == {"linear_ramp_setpoint", "hold", "profile"}
    assert schema["generators"]["discriminator"]["propertyName"] == "tag"
    assert schema["regulated"] == {} and schema["driven"] == {}

    # The units disagree (°C -> W), so the setpoint itself cannot be the feedforward.
    bad = client.post(
        "/api/controllers", json={"target": target, "source": source, "feedforward": "setpoint"}
    )
    assert bad.status_code == 409
    assert (
        client.post("/api/controllers", json={"target": target, "source": "nowhere.x"}).status_code
        == 404
    )
    assert (
        client.post("/api/controllers", json={"target": drive.name, "source": source}).status_code
        == 404
    )

    made = client.post(
        "/api/controllers",
        json={
            "target": target,
            "source": source,
            "law": {"tag": "P", "kp": 10.0},
            "default": True,
        },
    )
    assert made.status_code == 201
    body = made.json()
    assert body["name"] == target and body["target"] == target and body["source"] == source
    assert body["label"] == "Heater 1" and body["default"] is True and body["mode"] == "manual"
    assert body["feedforward"] == {"tag": "none"} and body["demand_unit"] == "W"
    assert body["law"]["tag"] == "P" and body["reading"] is None
    assert client.get(f"/api/controllers/{target}").json() == body
    assert client.get("/api/controllers/default").json() == body
    assert [c["name"] for c in client.get("/api/controllers").json()] == [target]
    schema = client.get("/api/controllers/schema").json()
    assert schema["regulated"] == {source: target} and schema["driven"] == {target: target}

    # The target and the source are spoken for.
    again = client.post("/api/controllers", json={"target": target, "source": f"{daq.name}.zone2"})
    assert again.status_code == 409 and "already driven" in again.json()["detail"]
    again = client.post(
        "/api/controllers", json={"target": f"{drive.name}.heater2", "source": source}
    )
    assert again.status_code == 409 and "already regulated" in again.json()["detail"]

    # A manual demand on a signal an active controller drives is refused; the
    # other heater is free, and so is this one while the controller is manual.
    assert client.put(f"/api/devices/{drive.name}/demand", json={"heater1": 5.0}).status_code == 200
    assert client.post(f"/api/controllers/{target}/regulate", json={"at": 50.0}).status_code == 200
    refused = client.put(f"/api/devices/{drive.name}/demand", json={"heater1": 5.0})
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        f"'{target}' is driven by controller '{target}':"
        " set its reference, put it in manual, or detach it"
    )
    assert client.put(f"/api/signals/{target}", json=5.0).status_code == 409
    assert client.put(f"/api/devices/{drive.name}/demand", json={"heater2": 5.0}).status_code == 200

    # Regulating commits the handover's demand at once.
    reg = client.post(f"/api/controllers/{target}/regulate", json={"at": 60.0, "transfer": "reset"})
    assert reg.status_code == 200
    assert reg.json()["mode"] == "regulating" and reg.json()["reference"] == 60.0
    assert reg.json()["demand"] == 0.0 and reg.json()["expected"] == 0.0
    assert drive.inputs["heater1"] == 0.0
    assert reg.json()["reading"] is None, "attached after the delivery: no tick yet"
    heater1 = client.get(f"/api/devices/{drive.name}").json()["signals"][1]
    assert heater1["write"]["controller"] == target and heater1["write"]["at_limit"] == "low"

    # A delivery steps the law: P with kp 10 on an error of 38.5 asks for 385 W.
    clock.advance(1.0)
    deliver(rig, daq)
    assert drive.inputs["heater1"] == 385.0
    after = client.get(f"/api/controllers/{target}").json()
    assert after["expected"] == 385.0
    assert after["reading"] == {"signal": source, "time_ns": 1_000_000_000, "value": 21.5}

    ref = client.put(f"/api/controllers/{target}/reference", json={"at": 65.0})
    assert ref.json()["reference"] == 65.0 and ref.json()["mode"] == "regulating"
    bad = client.post(f"/api/controllers/{target}/regulate", json={"at": 1.0, "tuning": "ghost"})
    assert bad.status_code == 404

    man = client.post(f"/api/controllers/{target}/manual")
    assert man.json()["mode"] == "manual"
    assert client.get("/api/health").json()["controllers"] == {target: "manual"}

    assert client.delete(f"/api/controllers/{target}").status_code == 204
    assert client.get("/api/controllers").json() == []
    assert client.delete(f"/api/controllers/{target}").status_code == 404
    assert client.put(f"/api/signals/{target}", json=5.0).status_code == 200, "free again"
    assert client.get(f"/api/controllers/{target}").status_code == 404


def test_tunings_are_stored_on_the_rig(client, rig):
    assert client.get("/api/tunings").json() == {}
    r = client.put("/api/tunings/gentle", json={"tag": "PI", "kp": 0.5, "ki": 0.1})
    assert r.status_code == 200 and r.json()["tag"] == "gentle"
    assert client.get("/api/tunings/gentle").json()["kp"] == 0.5
    assert client.get("/api/tunings/nope").status_code == 404
    assert rig.tunings.get("gentle") is not None


def test_reference_can_start_a_generator(client, rig, daq, drive, clock):
    """A generator spec on `/reference` shows its own params on the wire and moves the setpoint."""
    target, source = f"{drive.name}.heater1", f"{daq.name}.zone1"
    deliver(rig, daq)
    made = client.post(
        "/api/controllers",
        json={"target": target, "source": source, "law": {"tag": "P", "kp": 10.0}},
    )
    assert made.status_code == 201
    reg = client.post(f"/api/controllers/{target}/regulate", json={"at": 20.0, "transfer": "reset"})
    assert reg.status_code == 200 and reg.json()["setpoint"] == 20.0
    assert reg.json()["arrived"] is True, "a number is already where it is going"

    ramp = client.put(
        f"/api/controllers/{target}/reference",
        json={"at": {"tag": "linear_ramp_setpoint", "pace": {"per_minute": 10}, "end": 30.0}},
    )
    assert ramp.status_code == 200
    reference = ramp.json()["reference"]
    assert reference["tag"] == "linear_ramp_setpoint"
    assert reference["end"] == 30.0
    assert reference["pace"] == {"value": 10.0, "per": "minute"}
    assert reference["end_time"] == pytest.approx(60.0), "started: the wire says where it lands"
    assert ramp.json()["arrived"] is False

    # Mode and law untouched; the setpoint itself only moves once the law next ticks.
    assert ramp.json()["mode"] == "regulating" and ramp.json()["setpoint"] == 20.0

    clock.advance(1.0)
    deliver(rig, daq)
    after = client.get(f"/api/controllers/{target}").json()
    assert after["setpoint"] == pytest.approx(20.0 + 10.0 / 60.0)
    assert after["arrived"] is False

    clock.advance(59.0)
    deliver(rig, daq)
    landed = client.get(f"/api/controllers/{target}").json()
    assert landed["setpoint"] == 30.0 and landed["arrived"] is True

    bad = client.put(
        f"/api/controllers/{target}/reference", json={"at": {"tag": "no_such_generator"}}
    )
    assert bad.status_code == 422
    assert any("no_such_generator" in str(error) for error in bad.json()["detail"])


def test_reference_can_start_a_profile(client, rig, daq, drive, clock):
    """A profile's segments validate recursively; the wire shows them and the active index."""
    target, source = f"{drive.name}.heater1", f"{daq.name}.zone1"
    deliver(rig, daq)
    client.post(
        "/api/controllers",
        json={"target": target, "source": source, "law": {"tag": "P", "kp": 10.0}},
    )
    client.post(f"/api/controllers/{target}/regulate", json={"at": 20.0, "transfer": "reset"})
    profile = client.put(
        f"/api/controllers/{target}/reference",
        json={
            "at": {
                "tag": "profile",
                "segments": [
                    {"tag": "linear_ramp_setpoint", "pace": {"per_minute": 10}, "end": 30.0},
                    {"tag": "hold", "value": 30.0, "duration": {"minutes": 5}},
                    {"tag": "profile", "segments": [{"tag": "hold", "value": 25.0}]},
                ],
            }
        },
    )
    assert profile.status_code == 200, profile.text
    reference = profile.json()["reference"]
    assert reference["tag"] == "profile" and "end_time" not in reference, "endless at the end"
    assert [s["tag"] for s in reference["segments"]] == ["linear_ramp_setpoint", "hold", "profile"]
    assert reference["segments"][0]["pace"] == {"value": 10.0, "per": "minute"}
    assert reference["segments"][2]["segments"] == [
        {"tag": "hold", "value": 25.0, "duration": None}
    ]
    assert "active" not in reference, "not yet ticked"
    assert profile.json()["arrived"] is False

    clock.advance(30.0)
    deliver(rig, daq)
    ramping = client.get(f"/api/controllers/{target}").json()
    assert ramping["setpoint"] == pytest.approx(25.0)
    assert ramping["reference"]["active"] == 0

    clock.advance(60.0)
    deliver(rig, daq)
    soaking = client.get(f"/api/controllers/{target}").json()
    assert soaking["setpoint"] == 30.0 and soaking["reference"]["active"] == 1

    clock.advance(300.0)
    deliver(rig, daq)
    last = client.get(f"/api/controllers/{target}").json()
    assert last["setpoint"] == 25.0 and last["reference"]["active"] == 2
    assert last["arrived"] is False, "a hold with no duration never lands"

    endless_first = client.put(
        f"/api/controllers/{target}/reference",
        json={
            "at": {
                "tag": "profile",
                "segments": [{"tag": "hold", "value": 1.0}, {"tag": "hold", "value": 2.0}],
            }
        },
    )
    assert endless_first.status_code == 422
    assert "segment 0 (hold) never ends" in endless_first.text
    empty = client.put(
        f"/api/controllers/{target}/reference", json={"at": {"tag": "profile", "segments": []}}
    )
    assert empty.status_code == 422


def test_regulate_can_start_a_generator_from_the_current_reading(client, rig, daq, drive, clock):
    """`regulate` with a generator starts it from the reading when there is no reference yet."""
    target, source = f"{drive.name}.heater1", f"{daq.name}.zone1"
    client.post(
        "/api/controllers",
        json={"target": target, "source": source, "law": {"tag": "P", "kp": 10.0}},
    )
    deliver(rig, daq)  # zone1 reads 21.5, now that the controller is attached to see it
    started = client.post(
        f"/api/controllers/{target}/regulate",
        json={"at": {"tag": "linear_ramp_setpoint", "pace": {"per_minute": 10}, "end": 30.0}},
    )
    assert started.status_code == 200
    body = started.json()
    assert body["mode"] == "regulating"
    assert body["reference"]["tag"] == "linear_ramp_setpoint" and body["reference"]["end"] == 30.0
    assert body["setpoint"] == pytest.approx(21.5), "started from the last reading, not 0"


def test_regulate_a_generator_refuses_without_a_reading_or_reference(client, rig, daq, drive):
    target, source = f"{drive.name}.heater1", f"{daq.name}.zone1"
    client.post(
        "/api/controllers",
        json={"target": target, "source": source, "law": {"tag": "P", "kp": 10.0}},
    )
    refused = client.post(
        f"/api/controllers/{target}/regulate",
        json={"at": {"tag": "linear_ramp_setpoint", "pace": {"per_minute": 10}, "end": 30.0}},
    )
    assert refused.status_code == 503


def test_controllers_stream_sends_a_snapshot_then_each_tick(client, rig, daq, drive, clock):
    heater1, zone1 = drive.signals["heater1"], daq.signals["zone1"]
    controller = rig.attach_controller(heater1, zone1, law=P(kp=10.0))
    with client.websocket_connect("/ws/controllers") as ws:
        (first,) = ws.receive_json()["controllers"]
        assert first["name"] == heater1.address and first["mode"] == "manual"
        assert first["default"] is True and first["source"] == zone1.address
        deliver(rig, daq)
        controller.regulate(60.0, transfer=Transfer.RESET)
        clock.advance(1.0)
        deliver(rig, daq)
        frame = ws.receive_json()["controllers"]
        assert frame[-1]["mode"] == "regulating" and frame[-1]["expected"] == 385.0


def test_a_detached_controller_leaves_the_stream(client, rig, daq, drive, clock):
    heater1, zone1 = drive.signals["heater1"], daq.signals["zone1"]
    rig.attach_controller(heater1, zone1, law=P(kp=10.0))
    with client.websocket_connect("/ws/controllers") as ws:
        ws.receive_json()
        deliver(rig, daq)  # a state is published while someone watches
        ws.receive_json()
    assert client.delete(f"/api/controllers/{heater1.address}").status_code == 204
    heater2 = drive.signals["heater2"]
    rig.attach_controller(heater2, daq.signals["zone2"], law=P(kp=10.0))
    # Priming a new watcher used to crash on the detached name.
    with client.websocket_connect("/ws/controllers") as ws:
        names = {c["name"] for c in ws.receive_json()["controllers"]}
    assert names == {heater2.address}, "the detached controller's state cell is gone"


def test_a_generator_may_say_where_it_starts(client, rig, daq, drive, clock):
    """`start`: a value, or `setpoint` / `process` for the controller to resolve."""
    target, source = f"{drive.name}.heater1", f"{daq.name}.zone1"
    deliver(rig, daq)  # the reading is 21.5
    client.post(
        "/api/controllers",
        json={"target": target, "source": source, "law": {"tag": "P", "kp": 10.0}},
    )
    client.post(f"/api/controllers/{target}/regulate", json={"at": 20.0, "transfer": "reset"})
    ramp = {"tag": "linear_ramp_setpoint", "pace": {"minutes": 1}, "end": 80.0}

    from_value = client.put(
        f"/api/controllers/{target}/reference", json={"at": ramp, "start": 50.0}
    )
    assert from_value.status_code == 200
    clock.advance(30.0)
    deliver(rig, daq)
    assert client.get(f"/api/controllers/{target}").json()["setpoint"] == pytest.approx(65.0), (
        "50 → 80 over a minute, half way"
    )

    from_reading = client.put(
        f"/api/controllers/{target}/reference", json={"at": ramp, "start": "process"}
    )
    assert from_reading.status_code == 200
    clock.advance(30.0)
    deliver(rig, daq)
    assert client.get(f"/api/controllers/{target}").json()["setpoint"] == pytest.approx(
        (21.5 + 80.0) / 2
    ), "from the reading"

    from_setpoint = client.put(
        f"/api/controllers/{target}/reference", json={"at": ramp, "start": "setpoint"}
    )
    assert from_setpoint.status_code == 200
    before = (21.5 + 80.0) / 2
    clock.advance(30.0)
    deliver(rig, daq)
    assert client.get(f"/api/controllers/{target}").json()["setpoint"] == pytest.approx(
        (before + 80.0) / 2
    ), "from where the setpoint was"

    assert (
        client.put(
            f"/api/controllers/{target}/reference", json={"at": ramp, "start": "nowhere"}
        ).status_code
        == 422
    )
