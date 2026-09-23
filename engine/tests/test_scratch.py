"""The scratch record and what ages data out: kinds, pins, trimming, keeping, backfill, sweeps."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from conftest import FakeRunner, TestClient
from flyball.control import PI
from flyball.foundation.device import Access, Committable, Node, Readable, Role, Sample, SignalSpec
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Watt
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.deps import set_retention, set_runner, set_store
from flyball.record import SpanKind, SqliteStore, Window
from flyball.record.types import Event
from flyball.runtime.config import RunnerConfig, parse_duration_ns, parse_size_bytes
from flyball.runtime.retention import Retention

TEMP = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)
S = 1_000_000_000


class Oven(Readable, Committable):
    TREE = (
        SignalSpec(name="zone", quantity=TEMP, access=Access.RP),
        SignalSpec(
            name="heater", quantity=POWER, access=Access.RPW, role=Role.DEMAND, limits=(0.0, 100.0)
        ),
    )

    def __init__(self, name: str, label: str | None = None) -> None:
        super().__init__(name, label)
        self.zone = 20.0

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        yield Sample(self.root, time_ns, {self.signals["zone"]: self.zone})


@pytest.fixture
def oven(rig, fresh):
    oven = Oven(fresh("oven"))
    rig.add_device(oven)
    return oven


@pytest.fixture
def store(tmp_path):
    store = SqliteStore(tmp_path / "s.sqlite")
    yield store
    store.close()


def feed(rig, oven, clock, seconds: int, step_s: float = 1.0) -> None:
    """`seconds` of readings on `oven.zone`, one every `step_s`, the value being the time."""
    for _ in range(int(seconds / step_s)):
        clock.advance(step_s)
        rig.on_samples([Sample(oven.root, clock.now_ns(), {oven.signals["zone"]: clock.now_s()})])


def values(store, session_id, address, **window):
    span = Window(**window) if window else None
    return [p.value for p in store.series(session_id, address, span).points]


# region Parsing


@pytest.mark.parametrize(
    ("text", "ns"),
    [("1h", 3600 * S), ("30m", 1800 * S), ("90s", 90 * S), ("2d", 2 * 86400 * S)],
)
def test_durations(text, ns):
    assert parse_duration_ns(text) == ns


def test_duration_odd_spellings():
    assert parse_duration_ns("0") == 0 and parse_duration_ns(0) == 0
    assert parse_duration_ns("500ms") == 500_000_000 and parse_duration_ns("1.5H") == 5400 * S
    assert parse_duration_ns(2.5) == 2_500_000_000, "a bare number is seconds"
    with pytest.raises(ValueError, match="not a duration"):
        parse_duration_ns("soon")


def test_sizes():
    assert parse_size_bytes("256MB") == 256_000_000 and parse_size_bytes("20GB") == 20_000_000_000
    assert parse_size_bytes("1KiB") == 1024 and parse_size_bytes("1.5GiB") == 1_610_612_736
    assert parse_size_bytes("4096") == 4096 and parse_size_bytes("0") == 0
    with pytest.raises(ValueError, match="not a size"):
        parse_size_bytes("lots")


def test_the_runner_section_resolves_and_refuses_bad_spellings():
    settings = RunnerConfig(keep="2h", keep_size="1GiB", retain="30d", rotate="24h", max_store=0)
    assert (settings.keep_ns, settings.keep_bytes) == (7200 * S, 1_073_741_824)
    assert (settings.retain_ns, settings.rotate_ns, settings.max_bytes) == (
        30 * 86400 * S,
        86400 * S,
        0,
    )
    assert RunnerConfig().keep == "1h" and RunnerConfig().keep_size == "256MB"
    with pytest.raises(ValueError, match="keep"):
        RunnerConfig(keep="soon")
    with pytest.raises(ValueError, match="max_store"):
        RunnerConfig(max_store="lots")


# endregion

# region The store


def test_a_session_has_a_kind_and_a_pin_and_lists_by_kind(store):
    scratch = store.open_session(0, kind="scratch").session
    named = store.open_session(10).session
    assert scratch.scratch and scratch.kind == "scratch" and not named.scratch
    assert not named.pinned and store.set_pinned(named.id, True).pinned
    assert store.session(named.id).pinned and not store.set_pinned(named.id, False).pinned
    assert [s.id for s in store.sessions(kind="scratch")] == [scratch.id]
    assert [s.id for s in store.sessions(kind="session")] == [named.id]
    assert [s.id for s in store.sessions()] == [named.id, scratch.id]


def test_set_session_name_keeps_the_rest_of_details(store):
    session = store.open_session(0, details={"note": "kept"}).session
    renamed = store.set_session_name(session.id, "the good run")
    assert renamed.details == {"note": "kept", "name": "the good run"}
    again = store.set_session_name(session.id, "renamed again")
    assert again.details == {"note": "kept", "name": "renamed again"}
    cleared = store.set_session_name(session.id, None)
    assert cleared.details == {"note": "kept"}


def test_trimming_moves_the_start_up_and_offsets_stay_true(rig, oven, clock, store):
    """After a trim the row's start is the oldest kept and every read counts from there."""
    address = f"{oven.name}.zone"
    rig.start_recording(store, kind="scratch")
    session_id = rig.recorder.writer.session.id
    feed(rig, oven, clock, 10)  # readings at t = 1..10 s, valued t
    rig.recorder.flush()
    row = store.trim_session(session_id, 4 * S)
    assert row.start_ns == 4 * S and row.end_ns is None
    assert values(store, session_id, address) == [4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    series = store.series(session_id, address)
    assert [p.offset_ns for p in series.points][:2] == [0, S], "offsets from the new start"
    # A window is from the new start too: [1 s, 3 s) after it is t = 5, 6.
    assert values(store, session_id, address, start_ns=S, end_ns=3 * S) == [5.0, 6.0]
    # Buckets are laid from the new start: none is labelled before it.
    from flyball.record import Downsample

    bucketed = store.series(session_id, address, downsample=Downsample(bucket_ns=4 * S))
    assert [(p.offset_ns, p.value) for p in bucketed.points] == [(0, 5.5), (4 * S, 9.0)]
    # The writer carries on unaware: what it writes next still lines up.
    feed(rig, oven, clock, 2)
    rig.recorder.flush()
    assert values(store, session_id, address)[-2:] == [11.0, 12.0]
    assert store.samples(session_id, oven.name)[-1].offset_ns == 12 * S - 4 * S
    # Trimming to before the start, or twice, changes nothing; a closed row never past its end.
    assert store.trim_session(session_id, 2 * S).start_ns == 4 * S
    rig.stop_recording()
    assert store.trim_session(session_id, 99 * S).start_ns == store.session(session_id).end_ns
    assert values(store, session_id, address) == [12.0], "at the end itself: not before it"


def test_trimming_takes_ticks_states_events_and_closed_spans_too(rig, oven, clock, store):
    controller = rig.attach_controller(oven.signals["heater"], oven.signals["zone"], law=PI(kp=1.0))
    controller.regulate(50.0)
    rig.start_recording(store, kind="scratch")
    writer = rig.recorder.writer
    session_id = writer.session.id
    feed(rig, oven, clock, 6)
    rig.recorder.flush()
    writer.write_event(Event(2 * S, "note", "x"))
    writer.write_event(Event(5 * S, "note", "y"))
    done = writer.open_span(SpanKind.NOTE, "early", S)
    writer.close_span(done, 2 * S)
    still = writer.open_span(SpanKind.NOTE, "open", S)
    store.trim_session(session_id, 4 * S)
    assert [t.offset_ns for t in store.ticks(session_id, controller.name)] == [0, S, 2 * S]
    assert [w.offset_ns for w in store.write_states(session_id, controller.name)] == [0, S, 2 * S]
    assert [e.offset_ns for e in store.events(session_id)] == [S]
    (span,) = store.spans(session_id)
    assert span.id == still and span.start_ns == -3 * S, "an open span stays, dated from the start"
    rig.stop_recording()


def test_keep_range_is_a_closed_copy_rebased_to_its_start(rig, oven, clock, store):
    address = f"{oven.name}.zone"
    rig.start_recording(store, kind="scratch", config={"name": "lab"})
    scratch_id = rig.recorder.writer.session.id
    feed(rig, oven, clock, 10)
    clock.advance(0.5)
    rig.demand(oven.root, {"heater": 40.0})
    rig.recorder.flush()
    rig.recorder.writer.write_event(Event(int(6.5 * S), "note", "x", "kept"))
    kept = store.keep_range(scratch_id, 3 * S, 8 * S, details={"name": "the middle"})
    assert (kept.start_ns, kept.end_ns, kept.kind) == (3 * S, 8 * S, "session")
    assert kept.details == {"name": "the middle"} and kept.config == {"name": "lab"}
    assert not kept.open and kept.id != scratch_id
    assert values(store, kept.id, address) == [3.0, 4.0, 5.0, 6.0, 7.0]
    assert [p.offset_ns for p in store.series(kept.id, address).points][0] == 0
    assert {d.address for d in store.devices(kept.id)} == {oven.name}
    assert {s.address for s in store.signals(kept.id)} == {
        s.address for s in store.signals(scratch_id)
    }
    assert [w.address for w in store.writes(kept.id)] == [f"{oven.name}.heater"]
    assert [e.offset_ns for e in store.events(kept.id)] == [int(3.5 * S)]
    assert not store.write_states(kept.id, f"{oven.name}.heater"), "the demand was at 10.5 s"
    # The scratch record is untouched.
    assert len(values(store, scratch_id, address)) == 10
    # Outside what is held, or empty: refused.
    with pytest.raises(ValueError, match="empty"):
        store.keep_range(scratch_id, 5 * S, 5 * S)
    store.trim_session(scratch_id, 4 * S)
    with pytest.raises(ValueError, match="nothing before"):
        store.keep_range(scratch_id, 3 * S, 8 * S)
    rig.stop_recording()
    with pytest.raises(ValueError, match="ended at"):
        store.keep_range(scratch_id, 5 * S, 99 * S)
    # A kept session copies well after a trim too: the shift is undone on the way over.
    again = store.keep_range(scratch_id, 6 * S, 9 * S)
    assert values(store, again.id, address) == [6.0, 7.0, 8.0]


def test_backfill_puts_scratch_rows_under_a_recording_s_own(rig, oven, clock, store):
    address = f"{oven.name}.zone"
    rig.start_recording(store, kind="scratch")
    scratch_id = rig.recorder.writer.session.id
    feed(rig, oven, clock, 10)
    rig.recorder.flush()
    now = clock.now_ns()
    # The recording starts 4 s back; the recorder declares, then the scratch rows come over.
    recorder = rig.start_recording(store, start_ns=now - 4 * S)
    session_id = recorder.writer.session.id
    assert store.session(scratch_id).end_ns == now
    assert store.backfill(session_id, scratch_id, now - 4 * S, now + 99 * S) == 5, "to its end"
    feed(rig, oven, clock, 3)
    rig.stop_recording()
    assert values(store, session_id, address) == [6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0]
    seqs = [s.seq for s in store.samples(session_id, oven.name)]
    assert seqs == [-5, -4, -3, -2, -1, 1, 2, 3], "backfilled rows sit below the writer's count"
    assert [s.offset_ns for s in store.samples(session_id, oven.name)][:2] == [0, S]
    # Nothing to take: nothing copied.
    assert store.backfill(session_id, scratch_id, now + S, now + 2 * S) == 0


def test_measure_and_used_bytes(rig, oven, clock, store):
    rig.start_recording(store, kind="scratch")
    session_id = rig.recorder.writer.session.id
    assert store.session(session_id).bytes is None
    feed(rig, oven, clock, 100)
    rig.recorder.flush()
    size = store.measure_session(session_id)
    assert size == 100 * 56 and store.session(session_id).bytes == size
    before = store.used_bytes()
    feed(rig, oven, clock, 5000)
    rig.recorder.flush()
    assert store.used_bytes() > before
    rig.stop_recording()


def test_end_session_after_a_trim_ends_at_the_last_sample(rig, oven, clock, store):
    rig.start_recording(store, kind="scratch")
    session_id = rig.recorder.writer.session.id
    feed(rig, oven, clock, 10)
    rig.recorder.flush()
    rig.recorder._stop.set()  # leave the row open, as a runner that died would
    store.trim_session(session_id, 4 * S)
    assert store.end_session(session_id).end_ns == 10 * S


# endregion

# region The sweeps


def settings(**keys):
    return RunnerConfig(**keys)


def test_the_scratch_record_opens_on_start_and_again_after_a_recording(rig, oven, clock, store):
    retention = Retention(rig, store, settings(), period_s=3600)
    assert rig.recorder is None
    retention.start()
    try:
        scratch = retention.scratch
        assert scratch is not None and scratch.scratch and rig.recording is None
        assert scratch.config == {"name": rig.name} if rig.name else scratch.config is None
        started = rig.start_recording(store, details="run").writer.session
        assert rig.recording is not None and retention.scratch is None
        assert store.session(scratch.id).end_ns is not None, "replaced, so closed"
        rig.stop_recording()
        again = retention.scratch
        assert again is not None and again.id not in (scratch.id, started.id)
        assert store.session(started.id).end_ns is not None
    finally:
        retention.stop()
    rig.stop_recording()
    assert retention.scratch is None and rig.recorder is None, "stopped: not reopened"


def test_keep_zero_keeps_no_scratch(rig, store):
    retention = Retention(rig, store, settings(keep="0"), period_s=3600)
    retention.start()
    try:
        assert rig.recorder is None
        rig.start_recording(store)
        rig.stop_recording()
        assert rig.recorder is None
    finally:
        retention.stop()


def test_a_sweep_trims_scratch_to_keep_in_the_rig_s_clock(rig, oven, clock, store):
    address = f"{oven.name}.zone"
    retention = Retention(rig, store, settings(keep="5s"), period_s=3600)
    retention.start()
    try:
        feed(rig, oven, clock, 12)
        rig.recorder.flush()
        retention.sweep()
        scratch = retention.scratch
        assert scratch.start_ns == 7 * S and scratch.bytes == 6 * 56
        assert values(store, scratch.id, address) == [7.0, 8.0, 9.0, 10.0, 11.0, 12.0]
    finally:
        retention.stop()


def test_a_sweep_trims_scratch_under_keep_size(rig, oven, clock, store):
    retention = Retention(rig, store, settings(keep="1h", keep_size="2800"), period_s=3600)
    retention.start()
    try:
        feed(rig, oven, clock, 100)  # 100 readings ~ 5600 bytes by the estimate
        rig.recorder.flush()
        retention.sweep()
        scratch = retention.scratch
        assert scratch.bytes <= 2800 and 40 <= scratch.bytes // 56 <= 50
    finally:
        retention.stop()


def test_an_old_run_s_scratch_ages_out_whole(rig, oven, clock, store):
    old = store.open_session(0, kind="scratch")
    old.end(2 * S)
    clock.advance(100)
    retention = Retention(rig, store, settings(keep="10s"), period_s=3600)
    retention.start()
    try:
        assert [s.id for s in store.sessions(kind="scratch")] == [retention.scratch.id]
    finally:
        retention.stop()


def test_retain_deletes_ended_unpinned_recordings_only(rig, clock, store):
    a = store.open_session(0)
    a.end(S)
    b = store.open_session(0)
    b.end(S)
    store.set_pinned(b.session.id, True)
    c = store.open_session(0)  # still open: not retention's to close
    clock.advance(100)
    retention = Retention(rig, store, settings(keep="0", retain="10s"), period_s=3600)
    retention.start()
    try:
        assert {s.id for s in store.sessions()} == {b.session.id, c.session.id}
        rig.start_recording(store)
        clock.advance(100)
        retention.sweep()
        assert rig.recording is not None, "the recording in progress is never retained"
    finally:
        retention.stop()
        rig.stop_recording()


def test_rotate_continues_a_recording_at_the_boundary(rig, oven, clock, store):
    retention = Retention(rig, store, settings(keep="0", rotate="10s"), period_s=3600)
    retention.start()
    try:
        first = rig.start_recording(store, details={"name": "long"}).writer.session
        feed(rig, oven, clock, 8)
        retention.sweep()
        assert rig.recording.writer.session.id == first.id, "before the boundary"
        feed(rig, oven, clock, 3)
        retention.sweep()
        second = rig.recording.writer.session
        assert second.id != first.id and second.continues == first.id
        assert second.details == {"name": "long"} and store.session(first.id).end_ns is not None
        assert len(store.signals(second.id)) == len(store.signals(first.id))
    finally:
        retention.stop()
        rig.stop_recording()


def test_max_store_deletes_the_oldest_data_first_whatever_its_kind(rig, oven, clock, store):
    address = f"{oven.name}.zone"
    retention = Retention(rig, store, settings(keep="1h", max_store="1"), period_s=3600)
    # An old recording, an old pinned one, then scratch from the rig's start.
    old = store.open_session(-100 * S)
    old.end(-90 * S)
    pinned = store.open_session(-80 * S)
    pinned.end(-70 * S)
    store.set_pinned(pinned.session.id, True)
    retention.start()  # the first sweep already runs over the cap: the old recording goes
    try:
        assert store.sessions(kind="session") and {s.id for s in store.sessions()} == {
            pinned.session.id,
            retention.scratch.id,
        }
        feed(rig, oven, clock, 20)
        rig.recorder.flush()
        retention.sweep()
        scratch = retention.scratch
        assert scratch.start_ns > 0, "then scratch's oldest tenths, never the pinned session"
        assert len(values(store, scratch.id, address)) < 20
        assert store.session(pinned.session.id).pinned
    finally:
        retention.stop()


# endregion

# region Over the API


@pytest.fixture
def client(rig, oven, clock, store):
    set_rig(rig)
    set_store(store)
    policy = settings(keep="30s", rotate="1h")
    retention = Retention(rig, store, policy, period_s=3600)
    set_retention(retention)
    set_runner(FakeRunner(policy))
    retention.start()
    with TestClient(create_app()) as c:
        yield c
    retention.stop()
    set_runner(None)
    set_retention(None)
    set_store(None)
    set_rig(None)


def test_the_runner_reports_the_policy(client):
    runner = client.get("/api/runner").json()
    assert (runner["keep"], runner["keep_ns"]) == ("30s", 30 * S)
    assert (runner["keep_size"], runner["keep_bytes"]) == ("256MB", 256_000_000)
    assert (runner["retain"], runner["retain_ns"]) == ("0", 0)
    assert (runner["rotate"], runner["rotate_ns"]) == ("1h", 3600 * S)
    assert (runner["max_store"], runner["max_bytes"]) == ("0", 0)


def test_scratch_is_listed_but_is_not_the_recording(client, rig, oven, clock, store):
    assert client.get("/api/recording").json() is None
    assert client.get("/api/health").json()["recording"] is False
    (row,) = client.get("/api/history/sessions").json()
    assert row["kind"] == "scratch" and row["end_ns"] is None and row["pinned"] is False
    assert row["continues"] is None and "bytes" in row
    assert client.get("/api/history/sessions?kind=session").json() == []
    feed(rig, oven, clock, 3)
    rig.recorder.flush()
    series = client.get(f"/api/history/sessions/{row['id']}/series/{oven.name}.zone").json()
    assert len(series["points"]) == 3, "read like any other session"
    # It cannot be ended or deleted from under the runner.
    assert client.post(f"/api/history/sessions/{row['id']}/end").status_code == 409
    assert client.delete(f"/api/history/sessions/{row['id']}").status_code == 409


def test_keep_over_the_api(client, rig, oven, clock, store):
    scratch = client.get("/api/history/sessions").json()[0]
    feed(rig, oven, clock, 10)
    rig.recorder.flush()
    kept = client.post(
        f"/api/history/sessions/{scratch['id']}/keep",
        json={"start_ns": 2 * S, "end_ns": 6 * S, "details": {"name": "kept"}},
    )
    assert kept.status_code == 201, kept.text
    row = kept.json()
    assert (row["kind"], row["start_ns"], row["end_ns"], row["details"]) == (
        "session",
        2 * S,
        6 * S,
        {"name": "kept"},
    )
    assert values(store, row["id"], f"{oven.name}.zone") == [2.0, 3.0, 4.0, 5.0]
    future = client.post(
        f"/api/history/sessions/{scratch['id']}/keep", json={"start_ns": 2 * S, "end_ns": 99 * S}
    )
    assert future.status_code == 422 and "future" in future.json()["detail"]
    ids = [s["id"] for s in client.get("/api/history/sessions").json()]
    assert set(ids) == {scratch["id"], row["id"]}


def test_pin_over_the_api(client, store):
    session = store.open_session(0)
    session.end(S)
    sid = session.session.id
    assert client.put(f"/api/history/sessions/{sid}", json={"pinned": True}).json()["pinned"]
    assert store.session(sid).pinned
    assert not client.patch(f"/api/history/sessions/{sid}", json={"pinned": False}).json()["pinned"]
    assert client.put("/api/history/sessions/999", json={"pinned": True}).status_code == 404


def test_rename_over_the_api(client, store):
    session = store.open_session(0)
    session.end(S)
    sid = session.session.id
    body = client.patch(f"/api/history/sessions/{sid}", json={"name": "the good run"}).json()
    assert body["details"] == {"name": "the good run"}
    assert store.session(sid).details == {"name": "the good run"}
    # both fields in one call
    body = client.patch(
        f"/api/history/sessions/{sid}", json={"name": "renamed", "pinned": True}
    ).json()
    assert body["details"] == {"name": "renamed"} and body["pinned"]


def test_recording_with_include_ns_is_backfilled_from_scratch(client, rig, oven, clock, store):
    address = f"{oven.name}.zone"
    scratch = client.get("/api/history/sessions").json()[0]
    feed(rig, oven, clock, 10)
    rig.recorder.flush()
    started = client.post("/api/recording", json={"details": "x", "include_ns": 4 * S})
    assert started.status_code == 201, started.text
    session = started.json()
    assert session["start_ns"] == 6 * S and session["kind"] == "session"
    assert client.get("/api/recording").json()["id"] == session["id"]
    feed(rig, oven, clock, 2)
    ended = client.post("/api/recording/end").json()
    assert ended["id"] == session["id"] and ended["end_ns"] == 12 * S
    assert values(store, session["id"], address) == [6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0]
    rows = client.get("/api/history/sessions").json()
    kinds = {r["id"]: (r["kind"], r["end_ns"] is None) for r in rows}
    assert kinds[scratch["id"]] == ("scratch", False), "closed when the recording began"
    assert kinds[session["id"]] == ("session", False)
    (fresh,) = [r for r in rows if r["kind"] == "scratch" and r["end_ns"] is None]
    assert fresh["id"] not in (scratch["id"], session["id"]), "a new scratch record after"
    # Asking for more than is held gets what is held.
    clock.advance(1)
    more = client.post("/api/recording", json={"include_ns": 999 * S}).json()
    assert more["start_ns"] == store.session(fresh["id"]).start_ns
    client.post("/api/recording/end")


def test_include_ns_without_scratch_starts_now(rig, oven, clock, tmp_path):
    store = SqliteStore(tmp_path / "n.sqlite")
    set_rig(rig)
    set_store(store)
    try:
        with TestClient(create_app()) as c:
            clock.advance(5)
            session = c.post("/api/recording", json={"include_ns": 4 * S}).json()
            assert session["start_ns"] == 5 * S
            c.post("/api/recording/end")
    finally:
        set_store(None)
        set_rig(None)
        store.close()


# endregion
