"""Wave 2 stage 7: inputs as rig-owned bindings, and `driver: values`.

Number bindings, no defaults, staleness propagation, the cycle check, and the values
device with its store table.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import SecretStr, ValidationError

from conftest import TestClient
from flyball.control.laws import P
from flyball.foundation.actor import Actor
from flyball.foundation.device import (
    Access,
    Code,
    Committable,
    Demand,
    Device,
    DriverConfig,
    Input,
    InputBinding,
    LimitNotKnownError,
    Node,
    NoValue,
    NoValueError,
    Quality,
    Readable,
    Readout,
    Reason,
    Role,
    Sample,
    Severity,
    invalid,
    stale,
    values_of,
)
from flyball.foundation.errors import ConflictError, NotReadyError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Percent
from flyball.interfaces.server import create_app, set_rig
from flyball.model.catalog import get_catalog
from flyball.model.law import Transfer
from flyball.record import LiveValueRow
from flyball.record.sqlite import SqliteStore
from flyball.rig import Rig
from flyball.rig.stopping import InterimStopper
from flyball.runtime.config import RigConfig

HUMIDITY = Quantity("humidity", Percent)

BEN = Actor("ben", "human", "http")


class Source(Readable):
    """Something that publishes a level: what inputs follow."""

    level = Readout("level", "Level", HUMIDITY)
    other = Readout("other", "Other", HUMIDITY)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        yield self.sample(time_ns, level=50.0)


class Derived(Device):
    """No demands: `out` is twice its input, computed in `inputs_changed`."""

    x = Input("x", "X", HUMIDITY)
    out = Readout("out", "Out", HUMIDITY)

    def __init__(self, name: str, label: str | None = None) -> None:
        super().__init__(name, label)
        self.told: list[list[str]] = []

    def inputs_changed(self, time_ns: int, changed: list[InputBinding]) -> None:
        self.told.append([b.name for b in changed])
        try:
            (x,) = values_of(self.x)
        except NoValueError as error:
            self.out.push(error.no_value, time_ns)  # the input's quality, carried
            return
        except NotReadyError:
            return
        self.out.push(2.0 * x, time_ns)


class Clamped(Committable):
    """A demand whose top follows an input."""

    top = Input("top", "Top", HUMIDITY)
    target = Demand("target", "Target", HUMIDITY, limits=(0.0, top))
    chamber = Readout("chamber", "Chamber", HUMIDITY)


class Drive(Committable):
    power = Demand("power", "Power", HUMIDITY, limits=(0.0, 1000.0))


def _tag(fresh, device: type[Device]) -> str:
    """Register a driver for `device` in the catalog in force now: what `RigConfig` reads."""
    tag = fresh(device.__name__.lower())

    class Tagged(DriverConfig[device], type=tag):  # type: ignore[valid-type]
        def build(self, name: str, label: str | None = None) -> Device:
            return device(name, label)

    get_catalog().register_device(Tagged)
    return tag


@pytest.fixture
def source_tag(fresh) -> str:
    return _tag(fresh, Source)


@pytest.fixture
def derived_tag(fresh) -> str:
    return _tag(fresh, Derived)


def _push(rig: Rig, signal, value) -> None:
    rig.on_samples([Sample(signal.node, rig.clock.now_ns(), {signal: value})])


@pytest.fixture
def source(rig: Rig, fresh) -> Source:
    device = Source(fresh("src"))
    rig.add_device(device)
    return device


@pytest.fixture
def derived(rig: Rig, fresh) -> Derived:
    device = Derived(fresh("derived"))
    rig.add_device(device)
    return device


# region Number bindings and no defaults


class TestNumbersAndNoDefaults:
    def test_a_number_binding_has_a_value_from_build(self, derived_tag):
        rig = RigConfig.model_validate({
            "devices": {"d": {"driver": derived_tag, "inputs": {"x": 21}}}
        }).build(start=False)
        derived = rig.devices["d"]
        assert isinstance(derived, Derived)
        binding = derived.bound["x"]
        assert binding.constant == 21.0 and binding.follows is None and binding.bound
        assert binding.value == 21.0 and binding.quality is Quality.OK
        state = binding.state()
        assert (state.value, state.constant, state.follows, state.age_s) == (21.0, 21.0, None, None)
        assert state.unit == "%", "a number's unit is the declared input's"
        assert rig.latest[derived.signals["out"]].value == 42.0, "told once, at bind"
        assert rig.document()["devices"]["d"]["inputs"] == {"x": 21.0}

    def test_an_input_neither_bound_nor_a_number_is_refused_at_load(self, derived_tag):
        with pytest.raises(ValidationError, match=r"input 'x' is neither bound nor a number"):
            RigConfig.model_validate({"devices": {"d": {"driver": derived_tag}}})
        with pytest.raises(ValidationError, match=r"'y' is not an input of"):
            RigConfig.model_validate({
                "devices": {"d": {"driver": derived_tag, "inputs": {"x": 1.0, "y": 2.0}}}
            })
        schema = RigConfig.model_json_schema()
        variants = schema["properties"]["devices"]["additionalProperties"]["oneOf"]
        (variant,) = [v for v in variants if v.get("title") == derived_tag]
        assert "inputs" in variant["required"], "rig check (the schema) says so too"
        assert variant["properties"]["inputs"]["required"] == ["x"]

    def test_bind_inputs_refuses_a_declared_input_left_out(self, rig, derived):
        with pytest.raises(ConflictError, match=r"input 'x' is neither bound nor a number"):
            rig.bind_inputs(derived, {})

    def test_an_input_declares_no_default(self):
        with pytest.raises(TypeError, match="an input has no default"):
            Input("dry", "Dry", HUMIDITY, default=4.0)

    def test_an_entry_s_input_is_an_address_or_a_finite_number(self, derived_tag):
        for bad in (True, None, float("nan"), [1.0]):
            with pytest.raises(ValidationError):
                RigConfig.model_validate({
                    "devices": {"d": {"driver": derived_tag, "inputs": {"x": bad}}}
                })


# endregion

# region Address bindings: pending, limits, holds


class TestAddressBindings:
    def test_pending_until_the_first_reading_then_its_value_and_age(
        self, rig, source, derived, clock
    ):
        rig.bind_inputs(derived, {"x": f"{source.name}.level"})
        binding = derived.x
        assert binding is derived.bound["x"], "one object: the descriptor gives the binding"
        assert binding.signal is source.signals["level"]
        assert binding.quality is Quality.PENDING
        with pytest.raises(NotReadyError, match="has not been read yet"):
            _ = binding.value
        _push(rig, source.signals["level"], 10.0)
        clock.advance(2.0)
        state = binding.state()
        assert (state.value, state.quality) == (10.0, Quality.OK)
        assert state.follows == f"{source.name}.level"
        assert state.age_s == pytest.approx(2.0), "on the rig's clock, from when it arrived"

    def test_a_limit_following_an_input_fails_closed_and_names_its_quality(
        self, rig, source, fresh, clock
    ):
        clamped = Clamped(fresh("clamped"))
        rig.add_device(clamped)
        rig.bind_inputs(clamped, {"top": f"{source.name}.level"})
        target = clamped.signals["target"]
        with pytest.raises(LimitNotKnownError, match=r"'top' \(pending\)") as refused:
            rig.write(clamped.root, {target: 90.0})
        assert refused.value.benign
        _push(rig, source.signals["level"], 70.0)
        assert rig.write(clamped.root, {target: 90.0})[target].value == 70.0
        _push(rig, source.signals["level"], stale(Reason.DEVICE_OFFLINE))
        with pytest.raises(LimitNotKnownError, match=r"'top' \(stale: device_offline\)") as e:
            rig.write(clamped.root, {target: 90.0})
        assert not e.value.benign

    def test_a_controller_held_on_it_is_benign_while_pending_a_fault_once_stale(
        self, rig, source, fresh, clock
    ):
        clamped = Clamped(fresh("clamped"))
        rig.add_device(clamped)
        rig.bind_inputs(clamped, {"top": f"{source.name}.level"})
        target, chamber = clamped.signals["target"], clamped.signals["chamber"]
        controller = rig.attach_controller(target, chamber, law=P(kp=1.0))
        _push(rig, chamber, 40.0)
        controller.regulate(80.0, transfer=Transfer.COLD)
        held = rig.conditions.get(controller, Code.LIMIT_UNKNOWN)
        assert held is not None and held.severity is Severity.INFO, "pending: benign (A3)"
        _push(rig, source.signals["level"], stale(Reason.DEVICE_OFFLINE))
        clock.advance(1.0)
        _push(rig, chamber, 41.0)
        held = rig.conditions.get(controller, Code.LIMIT_UNKNOWN)
        assert held is not None and held.severity is Severity.WARNING, "stale: a fault"
        assert held.details["why"] == {"top": ("stale", "device_offline")}
        _push(rig, source.signals["level"], 70.0)
        clock.advance(1.0)
        _push(rig, chamber, 42.0)
        assert rig.conditions.get(controller, Code.LIMIT_UNKNOWN) is None


# endregion

# region Staleness propagation and evaluation order


class TestPropagation:
    def test_an_output_computed_from_an_input_carries_its_quality(self, rig, source, derived):
        rig.bind_inputs(derived, {"x": f"{source.name}.level"})
        out, level = derived.signals["out"], source.signals["level"]
        _push(rig, level, 10.0)
        assert rig.latest[out].value == 20.0
        _push(rig, level, stale(Reason.DEVICE_OFFLINE))
        assert rig.latest[out].value == NoValue(Quality.STALE, "device_offline")
        _push(rig, level, invalid("crc"))
        assert rig.latest[out].value == invalid("crc")
        _push(rig, level, 11.0)
        assert rig.latest[out].value == 22.0
        assert derived.told == [["x"]] * 4

    def test_values_of_carries_the_quality_that_ranks_first(self, rig, source, fresh):
        a, b = (
            rig.follow(f"{source.name}.level", owner="t", name="a"),
            rig.follow(f"{source.name}.other", owner="t", name="b"),
        )
        _push(rig, source.signals["level"], invalid("crc"))
        with pytest.raises(NotReadyError) as raised:
            values_of(a, b)
        assert not isinstance(raised.value, NoValueError), "pending ranks before invalid"
        _push(rig, source.signals["other"], stale(Reason.DEVICE_OFFLINE))
        with pytest.raises(NoValueError) as raised:
            values_of(a, b)
        assert raised.value.no_value.reason == "device_offline", "stale(device_*) ranks first"
        _push(rig, source.signals["level"], 1.0)
        _push(rig, source.signals["other"], 2.0)
        assert values_of(a, b) == (1.0, 2.0)

    def test_a_controller_measuring_a_derived_output_does_not_lag_a_delivery(
        self, rig, source, derived, fresh, clock
    ):
        rig.bind_inputs(derived, {"x": f"{source.name}.level"})
        drive = Drive(fresh("drive"))
        rig.add_device(drive)
        controller = rig.attach_controller(
            drive.signals["power"], derived.signals["out"], law=P(kp=1.0)
        )
        _push(rig, source.signals["level"], 10.0)
        controller.regulate(100.0, transfer=Transfer.COLD)
        for level in (20.0, 30.0):
            clock.advance(1.0)
            _push(rig, source.signals["level"], level)
            measured = controller.state.measured_value
            assert measured is not None and measured.value == 2 * level, (
                "stepped on the output this delivery computed, not the one before"
            )


# endregion

# region The binding's API: watchers, reverse lineage, unbinding


class TestTheBinding:
    def test_a_holder_that_is_not_a_device_watches_and_detaches(self, rig, source, derived):
        rig.bind_inputs(derived, {"x": f"{source.name}.level"})
        level = source.signals["level"]
        binding = rig.follow(f"{source.name}.level", owner="settle", name="level")
        seen: list[object] = []
        unwatch = binding.watch(lambda b: seen.append(b.value))
        _push(rig, level, 5.0)
        assert seen == [5.0]
        assert set(rig.consumers(level)) == {binding, derived.bound["x"]}
        unwatch()
        _push(rig, level, 6.0)
        assert seen == [5.0], "detached"
        rig.unbind(binding)
        assert rig.consumers(level) == [derived.bound["x"]]
        assert binding.quality is Quality.PENDING and not binding.bound

    def test_a_watcher_that_raises_is_logged_and_the_delivery_goes_on(self, rig, source):
        binding = rig.follow(f"{source.name}.level", owner="t", name="level")
        binding.watch(lambda b: 1 / 0)
        _push(rig, source.signals["level"], 5.0)
        assert rig.latest[source.signals["level"]].value == 5.0

    def test_a_namespace_binding_s_quality_is_the_worst_under_it(self, rig, source):
        binding = rig.follow(source.name, owner="t", name="all")
        assert binding.quality is Quality.PENDING
        _push(rig, source.signals["level"], 1.0)
        _push(rig, source.signals["other"], 2.0)
        assert binding.quality is Quality.OK
        _push(rig, source.signals["other"], invalid())
        assert binding.quality is Quality.INVALID
        with pytest.raises(TypeError, match="follows the namespace"):
            _ = binding.value

    def test_removing_the_source_leaves_the_input_pending(self, rig, source, derived):
        rig.bind_inputs(derived, {"x": f"{source.name}.level"})
        _push(rig, source.signals["level"], 5.0)
        rig.remove_device(source.name)
        assert derived.x.quality is Quality.PENDING and derived.x.follows is None
        assert "x" in derived.bound, "a declared input keeps its binding, unbound"


# endregion

# region Cycles


class TestCycles:
    def test_a_cycle_through_inputs_is_refused_at_load_with_its_path(self, derived_tag):
        with pytest.raises(
            ValidationError,
            match=r"a cycle through inputs: a\.inputs\.x <- b\.out; b\.inputs\.x <- a\.out",
        ):
            RigConfig.model_validate({
                "devices": {
                    "a": {"driver": derived_tag, "inputs": {"x": "b.out"}},
                    "b": {"driver": derived_tag, "inputs": {"x": "a.out"}},
                }
            })
        with pytest.raises(
            ValidationError, match=r"a cycle through inputs: a\.inputs\.x <- a\.out"
        ):
            RigConfig.model_validate({
                "devices": {"a": {"driver": derived_tag, "inputs": {"x": "a.out"}}}
            })

    def test_the_rig_refuses_a_cycle_and_keeps_nothing_bound(self, rig, fresh):
        a, b = Derived(fresh("a")), Derived(fresh("b"))
        rig.add_device(a)
        rig.add_device(b)
        rig.bind_inputs(a, {"x": f"{b.name}.out"})
        with pytest.raises(
            ConflictError,
            match=f"a cycle through inputs: {b.name}.inputs.x <- {a.name}.out;"
            f" {a.name}.inputs.x <- {b.name}.out",
        ):
            rig.bind_inputs(b, {"x": f"{a.name}.out"})
        assert not b.x.bound
        assert rig.consumers(a.signals["out"]) == []


# endregion

# region driver: values


def _values_rig(derived_tag: str, initial: float = 36.5, unit: str | None = "%") -> Rig:
    entry: dict[str, object] = {"initial": initial, "label": "Dry supply"}
    if unit is not None:
        entry["unit"] = unit
    return RigConfig.model_validate({
        "devices": {
            "bench": {"driver": "values", "values": {"dry": entry}},
            "d": {"driver": derived_tag, "inputs": {"x": "bench.dry"}},
        }
    }).build(start=False)


class TestValuesDevice:
    def test_published_at_build_so_an_input_on_it_is_never_pending(self, derived_tag):
        rig = _values_rig(derived_tag)
        bench, derived = rig.devices["bench"], rig.devices["d"]
        dry = bench.signals["dry"]
        assert (dry.role, dry.access, dry.unit.symbol) == (Role.SETTING, Access.RPW, "%")
        assert rig.latest[dry].value == 36.5
        assert derived.bound["x"].quality is Quality.OK
        assert rig.latest[derived.signals["out"]].value == 73.0
        assert dry not in rig.liveness.watches, "exempt from staleness"
        assert rig.values.source(dry).origin == "rig_file"

    def test_a_write_is_an_event_with_its_writer_and_reaches_what_follows_it(self, derived_tag):
        rig = _values_rig(derived_tag)
        bench, derived = rig.devices["bench"], rig.devices["d"]
        dry = bench.signals["dry"]
        rig.write(bench.root, {"dry": 40.0}, actor=BEN)
        assert rig.latest[dry].value == 40.0
        assert rig.latest[derived.signals["out"]].value == 80.0
        (written,) = [e for e in rig.recent if e.code == Code.VALUE_WRITTEN]
        assert written.subject == dry.address and written.details == {
            "value": 40.0,
            "was": 36.5,
            "actor": BEN.as_dict(),
        }
        source = rig.values.source(dry)
        assert (source.origin, source.actor) == ("written", BEN)

    def test_a_stop_leaves_it_alone(self, derived_tag):
        rig = _values_rig(derived_tag)
        report = InterimStopper(rig).stop(BEN, "test")
        assert "bench" not in report.devices

    def test_a_restart_restores_the_last_write_while_the_rig_file_agrees(
        self, derived_tag, tmp_path
    ):
        store = SqliteStore(tmp_path / "s.sqlite")
        first = _values_rig(derived_tag)
        first.values.attach(store)
        first.write(first.devices["bench"].root, {"dry": 40.0}, actor=BEN)
        (row,) = store.live_values()
        assert (row.device, row.signal, row.kind, row.value, row.unit, row.initial, row.actor) == (
            "bench",
            "dry",
            "value",
            40.0,
            "%",
            36.5,
            BEN,
        )

        again = _values_rig(derived_tag)
        again.values.attach(store)
        dry = again.devices["bench"].signals["dry"]
        assert again.latest[dry].value == 40.0
        assert again.latest[again.devices["d"].signals["out"]].value == 80.0
        source = again.values.source(dry)
        assert (source.origin, source.actor, source.written_utc_ns) == (
            "restored",
            BEN,
            row.written_utc_ns,
        )
        assert [e.code for e in again.recent if e.code == Code.VALUE_RESTORED] == ["value_restored"]

        unit = _values_rig(derived_tag, unit=None)
        unit.values.attach(store)
        dry = unit.devices["bench"].signals["dry"]
        assert unit.latest[dry].value == 36.5, "the unit changed: the file wins"
        held = unit.conditions.get(dry, Code.VALUE_NOT_RESTORED)
        assert held is not None and held.severity is Severity.WARNING

        edited = _values_rig(derived_tag, initial=37.0)
        edited.values.attach(store)
        dry = edited.devices["bench"].signals["dry"]
        assert edited.latest[dry].value == 37.0, "the file was edited since: it wins"
        assert edited.values.source(dry).origin == "rig_file"
        assert store.live_values() == [], "and the kept value is forgotten"
        store.close()

    def test_the_store_never_keeps_a_secret(self, tmp_path):
        store = SqliteStore(tmp_path / "s.sqlite")
        with pytest.raises(ValueError, match="a secret is never kept"):
            store.put_live_value(
                LiveValueRow("d", "s", "setting", SecretStr("x"), None, None, None, 0)
            )
        store.close()

    def test_the_wire_shows_lineage_consumers_and_the_value_s_source(self, derived_tag):
        rig = _values_rig(derived_tag)
        set_rig(rig)
        with TestClient(create_app()) as client:
            r = client.put("/api/signals/bench.dry", json=41.0)
            assert r.status_code == 200, r.text
            bench = client.get("/api/devices/bench").json()
            derived = client.get("/api/devices/d").json()
            schema = client.get("/api/devices/d/schema").json()
        assert bench["consumers"] == {"dry": ["d.inputs.x"]}
        source = bench["sources"]["dry"]
        assert source["origin"] == "written" and source["initial"] == 36.5
        assert source["actor"]["principal"] == "local:console", "the principal who wrote it"
        (x,) = derived["inputs"].values()
        assert x["bound"] == "bench.dry" and x["quality"] == "ok" and "constant" not in x
        assert x["unit"] == "%" and x["label"] == "X"
        assert schema["inputs"]["x"]["bound"] == "bench.dry"


# endregion


class TestRecordFalse:
    def test_a_signal_marked_record_false_is_left_out_of_a_default_recording(
        self, source_tag, tmp_path
    ):
        rig = RigConfig.model_validate({
            "devices": {"s": {"driver": source_tag, "signals": {"other": {"record": False}}}}
        }).build(start=False)
        store = SqliteStore(tmp_path / "s.sqlite")
        recorder = rig.start_recording(store)
        s = rig.devices["s"]
        assert s.signals["level"] in recorder.signals
        assert s.signals["other"] not in recorder.signals
        assert not s.signals["other"].spec.record
        rig.stop_recording()
        store.close()
