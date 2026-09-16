"""A recorded session as Bluesky event-model documents."""

from __future__ import annotations

import json
from collections.abc import Iterator

from flyball.control import PI
from flyball.core.device import Device
from flyball.core.quantity import Quantity
from flyball.core.signal import Access, Node, Sample, SignalSpec
from flyball.core.units.si import Celsius, Watt
from flyball.db.documents import documents, write_jsonl
from flyball.db.sqlite import SqliteStore


class Oven(Device):
    """One RP temperature and one W heater: a source stream and a controller stream."""

    TREE = (
        SignalSpec(name="temperature", quantity=Quantity("temperature", Celsius), access=Access.RP),
        SignalSpec(name="heater", quantity=Quantity("power", Watt), access=Access.W),
    )

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        yield Sample(self.root, time_ns, {self.signals["temperature"]: 20.0})


def _record(rig, oven, clock, n=5):
    controller = rig.attach_controller(
        oven.signals["heater"], oven.signals["temperature"], law=PI(kp=1.0)
    )
    controller.regulate(10.0)
    store = SqliteStore(":memory:")
    rig.start_recording(store)
    for i in range(n):
        clock.advance(0.5)
        rig.on_samples([Sample(oven.root, clock.now_ns(), {oven.signals["temperature"]: 20.0 + i})])
    rig.stop_recording()
    return store, store.sessions()[0]


def test_session_becomes_start_descriptors_events_stop(rig, fresh, clock):
    oven = Oven(fresh("oven"))
    rig.add_device(oven)
    store, session = _record(rig, oven, clock)
    docs = list(documents(store, session.id))
    names = [name for name, _ in docs]
    assert names[0] == "start" and names[-1] == "stop"
    assert names.count("descriptor") == 2, "one stream per device, one per controller"
    assert names.count("event") == 10, "five samples and five ticks"

    start = docs[0][1]
    assert start["plan_name"] == "flyball" and start["flyball"]["session"] == session.id
    assert start["time"] == session.start_ns / 1e9 and start["detectors"] == [oven.name]

    descriptor = next(d for n, d in docs if n == "descriptor" and d["name"] == oven.name)
    key = f"{oven.name}.temperature"
    assert descriptor["data_keys"][key] == {
        "source": f"flyball:{key}",
        "dtype": "number",
        "shape": [],
        "units": "°C",
    }
    assert f"{oven.name}.heater" in descriptor["data_keys"], "a written signal is a key too"
    assert descriptor["run_start"] == start["uid"]

    events = [d for n, d in docs if n == "event" and d["descriptor"] == descriptor["uid"]]
    assert [e["data"][key] for e in events] == [20.0, 21.0, 22.0, 23.0, 24.0]
    assert [e["seq_num"] for e in events] == [1, 2, 3, 4, 5]
    assert events[0]["timestamps"][key] == events[0]["time"] > start["time"]

    stream = f"controller:{oven.name}.heater"
    controller_descriptor = next(d for n, d in docs if n == "descriptor" and d["name"] == stream)
    assert controller_descriptor["configuration"][stream]["data"]["law"]["tag"] == "PI"
    assert controller_descriptor["data_keys"][f"{stream}.setpoint"]["units"] == "°C"
    tick = next(
        d for n, d in docs if n == "event" and d["descriptor"] == controller_descriptor["uid"]
    )
    assert tick["data"][f"{stream}.setpoint"] == 10.0 and f"{stream}.reading" in tick["data"]

    stop = docs[-1][1]
    assert stop["exit_status"] == "success" and stop["run_start"] == start["uid"]
    assert stop["num_events"] == {oven.name: 5, stream: 5}


def test_jsonl_is_one_document_per_line(rig, fresh, clock, tmp_path):
    oven = Oven(fresh("oven"))
    rig.add_device(oven)
    store, session = _record(rig, oven, clock, n=2)
    path = tmp_path / "run.jsonl"
    count = write_jsonl(store, session.id, path)
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert count == len(lines) == 1 + 2 + 4 + 1
    assert lines[0]["name"] == "start" and lines[-1]["name"] == "stop"
