"""A recorded session as Bluesky event-model documents."""

from __future__ import annotations

import json

from flyball.control import PI
from flyball.db.documents import documents, write_jsonl
from flyball.db.sqlite import SqliteStore
from helpers import sample


def _record(rig, probe, temperature, heater, clock, n=5):
    rig.attach_loop(probe[temperature], heater, law=PI(kp=1.0))
    rig.loops[heater.name].regulate(10.0)
    store = SqliteStore(":memory:")
    rig.start_recording(store)
    for i in range(n):
        clock.advance(0.5)
        rig.on_read([sample(probe, temperature, 20.0 + i, clock.now_ns(), seq=i + 1)])
    rig.stop_recording()
    return store, store.sessions()[0]


def test_session_becomes_start_descriptors_events_stop(rig, probe, temperature, heater, clock):
    store, session = _record(rig, probe, temperature, heater, clock)
    docs = list(documents(store, session.id))
    names = [name for name, _ in docs]
    assert names[0] == "start" and names[-1] == "stop"
    assert names.count("descriptor") == 2, "one stream per source, one per loop"
    assert names.count("event") == 10, "five samples and five ticks"

    start = docs[0][1]
    assert start["plan_name"] == "flyball" and start["flyball"]["session"] == session.id
    assert start["time"] == session.start_ns / 1e9

    descriptor = next(d for n, d in docs if n == "descriptor" and d["name"] == str(probe.name))
    key = f"{probe.name}.{temperature.name}"
    assert descriptor["data_keys"][key] == {
        "source": f"flyball:{key}",
        "dtype": "number",
        "shape": [],
        "units": "°C",
    }
    assert descriptor["run_start"] == start["uid"]

    events = [d for n, d in docs if n == "event" and d["descriptor"] == descriptor["uid"]]
    assert [e["data"][key] for e in events] == [20.0, 21.0, 22.0, 23.0, 24.0]
    assert [e["seq_num"] for e in events] == [1, 2, 3, 4, 5]
    assert events[0]["timestamps"][key] == events[0]["time"] > start["time"]

    loop_stream = f"loop:{heater.name}"
    loop_descriptor = next(d for n, d in docs if n == "descriptor" and d["name"] == loop_stream)
    assert loop_descriptor["configuration"][loop_stream]["data"]["law"]["tag"] == "PI"
    tick = next(d for n, d in docs if n == "event" and d["descriptor"] == loop_descriptor["uid"])
    assert (
        tick["data"][f"{loop_stream}.setpoint"] == 10.0 and f"{loop_stream}.reading" in tick["data"]
    )

    stop = docs[-1][1]
    assert stop["exit_status"] == "success" and stop["run_start"] == start["uid"]
    assert stop["num_events"] == {str(probe.name): 5, loop_stream: 5}


def test_jsonl_is_one_document_per_line(rig, probe, temperature, heater, clock, tmp_path):
    store, session = _record(rig, probe, temperature, heater, clock, n=2)
    path = tmp_path / "run.jsonl"
    count = write_jsonl(store, session.id, path)
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert count == len(lines) == 1 + 2 + 4 + 1
    assert lines[0]["name"] == "start" and lines[-1]["name"] == "stop"
