"""`driver: curve`: one signal through a calibration, computed in the delivery, none out of domain.

The derived device of sensor-generalisation P4: evaluation in `inputs_changed`, the input's
no-value carried, no extrapolation, refused at load when the curve or a cycle is wrong, not
stoppable, and a raw source left out of a recording by `record: false`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from flyball_sim import SteppedClock
from pydantic import ValidationError

from conftest import TestClient
from flyball.control.laws import P
from flyball.foundation.device import (
    Committable,
    Demand,
    DriverConfig,
    Node,
    NoValue,
    Quality,
    Readable,
    Readout,
    Reason,
    Sample,
    invalid,
    stale,
)
from flyball.foundation.device.derived import Curve, CurveConfig
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.curves import Linear, Table
from flyball.foundation.quantities.si import Volt, Watt
from flyball.interfaces.server import create_app, set_rig
from flyball.model.catalog import get_catalog
from flyball.model.law import Transfer
from flyball.record.sqlite import SqliteStore
from flyball.rig import Rig
from flyball.rig.latches import stoppable
from flyball.runtime.config import RigConfig

VOLTAGE = Quantity("voltage", Volt)


class Adc(Readable):
    """One raw channel, in volts: what a curve calibrates."""

    raw_v = Readout("raw_v", "Raw", VOLTAGE)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        yield self.sample(time_ns, raw_v=3.0)


class Heater(Committable):
    power = Demand("power", "Power", Quantity("power", Watt), limits=(0.0, 1000.0))


@pytest.fixture
def adc_tag(fresh) -> str:
    tag = fresh("adc")

    class AdcConfig(DriverConfig[Adc], type=tag):
        def build(self, name: str, label: str | None = None) -> Adc:
            return Adc(name, label)

    get_catalog().register_device(AdcConfig)
    return tag


TABLE = {"type": "table", "points": [[2.5, 3000.0], [3.5, 1000.0], [4.2, 0.0]]}


def _rig(adc_tag: str, curve: dict[str, Any] = TABLE, **entry: Any) -> Rig:
    return RigConfig.model_validate({
        "devices": {
            "adc": {"driver": adc_tag, **entry.pop("adc", {})},
            "turbidity": {
                "driver": "curve",
                "inputs": {"x": "adc.raw_v"},
                "curve": curve,
                "unit": "V",
                **entry,
            },
        }
    }).build(clock=SteppedClock(0), start=False)


def _push(rig: Rig, signal, value) -> None:
    rig.on_samples([Sample(signal.node, rig.clock.now_ns(), {signal: value})])


# region Evaluation


class TestEvaluation:
    def test_a_table_maps_the_input_in_the_delivery_that_brings_it(self, adc_tag):
        rig = _rig(adc_tag)
        raw, value = rig.resolve("adc.raw_v"), rig.resolve("turbidity.value")
        assert rig.latest.get(value) is None, "pending: nothing pushed before a first reading"
        _push(rig, raw, 3.0)
        assert rig.latest[value].value == pytest.approx(2000.0)
        _push(rig, raw, 4.2)
        assert rig.latest[value].value == pytest.approx(0.0, abs=1e-9)

    def test_a_linear_curve_and_the_readout_s_unit_and_quantity(self, adc_tag):
        linear = {"type": "linear", "scale": 25.0, "offset": -12.5}
        rig = _rig(adc_tag, linear, unit="kPa", quantity="pressure", label="Line pressure")
        value = rig.resolve("turbidity.value")
        _push(rig, rig.resolve("adc.raw_v"), 2.0)
        assert rig.latest[value].value == 37.5
        assert value.unit.symbol == "kPa" and value.quantity.name == "pressure"
        assert str(value.access) == "rp"
        assert rig.devices["turbidity"].label == "Line pressure"

    @pytest.mark.parametrize(("x", "side"), [(2.4, "low"), (4.3, "high")])
    def test_out_of_domain_has_no_value_never_an_extrapolation(self, adc_tag, x, side):
        rig = _rig(adc_tag)
        raw, value = rig.resolve("adc.raw_v"), rig.resolve("turbidity.value")
        _push(rig, raw, x)
        assert rig.latest[value].value == invalid("out_of_domain", side)
        assert rig.latest[value].quality is Quality.INVALID
        _push(rig, raw, 3.5)
        assert rig.latest[value].value == 1000.0, "back in domain: a value again"

    def test_an_input_with_no_value_gives_its_no_value(self, adc_tag):
        rig = _rig(adc_tag)
        raw, value = rig.resolve("adc.raw_v"), rig.resolve("turbidity.value")
        _push(rig, raw, invalid("crc"))
        assert rig.latest[value].value == invalid("crc")
        _push(rig, raw, stale(Reason.DEVICE_OFFLINE))
        assert rig.latest[value].value == NoValue(Quality.STALE, "device_offline")
        _push(rig, raw, 3.0)
        assert rig.latest[value].value == pytest.approx(2000.0)

    def test_a_number_input_gives_a_value_from_build(self, fresh):
        rig = RigConfig.model_validate({
            "devices": {"k": {"driver": "curve", "inputs": {"x": 3.0}, "curve": TABLE}}
        }).build(start=False)
        curve = rig.devices["k"]
        assert isinstance(curve, Curve)
        assert rig.latest[curve.value].value == pytest.approx(2000.0)

    def test_a_controller_measuring_the_output_does_not_lag_a_delivery(self, adc_tag, fresh, rig):
        adc = Adc(fresh("adc"))
        curve = Curve(fresh("curve"), Linear(10.0), VOLTAGE)
        heater = Heater(fresh("heater"))
        for device in (adc, curve, heater):
            rig.add_device(device)
        rig.bind_inputs(curve, {"x": f"{adc.name}.raw_v"})
        controller = rig.attach_controller(heater.signals["power"], curve.value, law=P(kp=1.0))
        _push(rig, adc.signals["raw_v"], 1.0)
        controller.regulate(100.0, transfer=Transfer.COLD)
        for x in (2.0, 3.0):
            rig.clock.advance(1.0)  # type: ignore[attr-defined]
            _push(rig, adc.signals["raw_v"], x)
            measured = controller.state.measured_value
            assert measured is not None and measured.value == 10 * x, (
                "stepped on the value this delivery computed, not the one before"
            )


# endregion

# region Refused at load


class TestRefusedAtLoad:
    @pytest.mark.parametrize(
        ("curve", "said"),
        [
            ({"type": "table", "points": [[0.0, 0.0]] * 1025}, "at most 1024 points"),
            ({"type": "table", "points": [[0.0, float("inf")]]}, "y must be finite"),
            ({"type": "table", "points": []}, "at least one point"),
            ({"type": "linear", "scale": float("nan")}, "scale must be finite"),
            ({"type": "cubic", "points": []}, "does not match any of the expected tags"),
            ({"type": "linear", "scale": 1.0, "gain": 2.0}, "Extra inputs are not permitted"),
        ],
    )
    def test_a_curve_that_does_not_build(self, curve, said):
        with pytest.raises(ValidationError, match=said):
            CurveConfig.model_validate({"curve": curve})

    def test_an_unknown_unit(self):
        with pytest.raises(ValidationError, match="No unit with symbol 'furlong'"):
            CurveConfig.model_validate({"curve": TABLE, "unit": "furlong"})

    def test_the_input_is_required(self):
        with pytest.raises(ValidationError, match="input 'x' is neither bound nor a number"):
            RigConfig.model_validate({"devices": {"k": {"driver": "curve", "curve": TABLE}}})

    def test_a_curve_feeding_itself_or_two_feeding_each_other(self):
        def entry(source: str) -> dict[str, Any]:
            return {"driver": "curve", "inputs": {"x": source}, "curve": TABLE}

        with pytest.raises(
            ValidationError, match=r"a cycle through inputs: a\.inputs\.x <- a\.value"
        ):
            RigConfig.model_validate({"devices": {"a": entry("a.value")}})
        with pytest.raises(
            ValidationError,
            match=r"a cycle through inputs: a\.inputs\.x <- b\.value; b\.inputs\.x <- a\.value",
        ):
            RigConfig.model_validate({"devices": {"a": entry("b.value"), "b": entry("a.value")}})


# endregion


class TestStopAndRecording:
    def test_not_stoppable(self, adc_tag):
        rig = _rig(adc_tag)
        curve = rig.devices["turbidity"]
        assert not curve.demands and curve.stops_by() is None
        assert not stoppable(curve)

    def test_a_raw_source_marked_record_false_leaves_no_rows_the_output_does(
        self, adc_tag, tmp_path
    ):
        rig = _rig(adc_tag, adc={"signals": {"raw_v": {"record": False}}})
        raw = rig.resolve("adc.raw_v")
        store = SqliteStore(tmp_path / "s.sqlite")
        recorder = rig.start_recording(store)
        session = recorder.writer.session.id
        for x in (3.0, 3.5):
            rig.clock.advance(1.0)
            _push(rig, raw, x)
        rig.stop_recording()
        declared = {s.address for s in store.signals(session)}
        assert "turbidity.value" in declared and "adc.raw_v" not in declared
        stored = [row.values for row in store.samples(session, "turbidity")]
        assert [v["turbidity.value"] for v in stored] == pytest.approx([2000.0, 1000.0])
        assert "adc" not in {d.address for d in store.devices(session)}, "nothing of it stored"
        assert rig.latest[raw].value == 3.5, "record: false is store-only: still live"
        store.close()


class TestRawOnTheWire:
    def test_the_engineering_signal_names_its_raw_one_and_the_raw_one_its_engineering(
        self, adc_tag
    ):
        rig = RigConfig.model_validate({
            "devices": {
                "adc": {"driver": adc_tag},
                "turbidity": {"driver": "curve", "inputs": {"x": "adc.raw_v"}, "curve": TABLE},
                "scaled": {
                    "driver": "curve",
                    "inputs": {"x": "adc.raw_v"},
                    "curve": {"type": "linear", "scale": 2.0},
                },
                "fixed": {"driver": "curve", "inputs": {"x": 3.0}, "curve": TABLE},
            }
        }).build(start=False)
        set_rig(rig)
        with TestClient(create_app()) as client:
            adc = client.get("/api/devices/adc").json()
            turbidity = client.get("/api/devices/turbidity").json()
            fixed = client.get("/api/devices/fixed").json()
            schema = client.get("/api/schema").json()["devices"]
            one = client.get("/api/devices/adc/schema").json()
        (raw_v,) = adc["signals"]
        assert raw_v["raw_for"] == ["turbidity.value", "scaled.value"] and "raw" not in raw_v
        assert adc["consumers"] == {"raw_v": ["turbidity.inputs.x", "scaled.inputs.x"]}
        (value,) = turbidity["signals"]
        assert value["raw"] == "adc.raw_v" and "raw_for" not in value
        (value,) = fixed["signals"]
        assert "raw" not in value and "raw_for" not in value, "a number is not a raw signal"
        assert schema["adc"]["signals"]["raw_v"]["raw_for"] == ["turbidity.value", "scaled.value"]
        assert schema["scaled"]["signals"]["value"]["raw"] == "adc.raw_v"
        assert "raw" not in schema["fixed"]["signals"]["value"]
        assert one["signals"]["raw_v"]["raw_for"] == ["turbidity.value", "scaled.value"]

    def test_a_signal_nothing_derives_from_carries_neither(self, adc_tag):
        rig = RigConfig.model_validate({"devices": {"adc": {"driver": adc_tag}}}).build(start=False)
        set_rig(rig)
        with TestClient(create_app()) as client:
            (raw_v,) = client.get("/api/devices/adc").json()["signals"]
        assert "raw" not in raw_v and "raw_for" not in raw_v


def test_built_in_code_describes_itself():
    curve = Curve("k", Table(((0.0, 0.0), (1.0, 10.0))), VOLTAGE)
    assert curve.config.model_dump(exclude={"link"}) == {
        "curve": {"type": "table", "points": [(0.0, 0.0), (1.0, 10.0)]},
        "unit": "V",
        "quantity": "voltage",
    }
