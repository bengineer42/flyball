"""The QCoDeS and PyMeasure devices, against fakes that mimic each library's interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import ValidationError

from flyball.core.signal import Access, Role, Signal
from flyball.integrations.pymeasure import (
    PyMeasure,
    PyMeasureSignal,
    properties,
    unit_from_doc,
)
from flyball.integrations.qcodes import QCoDeS, QCoDeSSignal, bounds, unit_for

NS = 1_000_000_000

# region QCoDeS fakes


class Numbers:
    def __init__(self, min_value=-1e300, max_value=1e300):
        self.min_value, self.max_value = min_value, max_value


class FakeParameter:
    """The parts of ``qcodes.Parameter`` the wrapper reads."""

    def __init__(
        self, name, unit="", label="", value: Any = 0.0, settable=True, gettable=True, vals=None
    ):
        self.name, self.unit, self.label, self._value = name, unit, label, value
        self.settable, self.gettable, self.vals = settable, gettable, vals
        self.sets: list[float] = []

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        self.sets.append(value)


@dataclass
class FakeSubmodule:
    parameters: dict[str, FakeParameter]


class FakeInstrument:
    def __init__(self, name):
        self.name = name
        self.parameters: dict[str, FakeParameter] = {
            "IDN": FakeParameter("IDN", value={"vendor": "fake"}, settable=False),
            "volt": FakeParameter("volt", "V", "Voltage", 1.25, vals=Numbers(-10, 10)),
            "curr": FakeParameter("curr", "A", "Current", 0.002, settable=False),
            "temp": FakeParameter("temp", "degC", "Temperature", 21.0, settable=False),
        }
        self.source: FakeSubmodule | None = None


# endregion


class TestQCoDeS:
    def test_a_settable_parameter_is_a_demand_and_rpw(self, fresh):
        inst = FakeInstrument("smu")
        device = QCoDeS(fresh("smu"), inst, {"bias": QCoDeSSignal(property="volt")})
        assert device.signals["bias"].role is Role.DEMAND
        assert device.signals["bias"].access is Access.RPW, "a demand, whatever `publish` says"
        assert "bias" in device.publishing, "a demand publishes its readback"

    def test_a_read_only_parameter_is_an_output(self, fresh):
        inst = FakeInstrument("smu")
        device = QCoDeS(fresh("smu"), inst, {"temp": QCoDeSSignal(property="temp")})
        assert device.signals["temp"].role is Role.OUTPUT
        assert device.signals["temp"].access is Access.R
        assert "temp" not in device.publishing, "not streamed unless published"

    def test_a_read_only_parameter_published_is_rp(self, fresh):
        inst = FakeInstrument("smu")
        device = QCoDeS(
            fresh("smu"),
            inst,
            {"temperature": QCoDeSSignal(property="temp", unit="°C", publish=True)},
        )
        assert device.signals["temperature"].access is Access.RP
        assert device.signals["temperature"].quantity.unit.symbol == "°C", "override applied"

    def test_publish_without_a_getter_is_refused(self, fresh):
        inst = FakeInstrument("smu")
        inst.parameters["write_only"] = FakeParameter("write_only", gettable=False)
        with pytest.raises(ValueError, match="publish needs a gettable parameter"):
            QCoDeS(fresh("smu"), inst, {"x": QCoDeSSignal(property="write_only", publish=True)})

    def test_read_yields_only_due_published_signals(self, fresh):
        inst = FakeInstrument("smu")
        device = QCoDeS(
            fresh("smu"),
            inst,
            {
                "volt": QCoDeSSignal(property="volt", publish=True),
                "curr": QCoDeSSignal(property="curr"),
            },
        )
        samples = list(device.read(5))
        assert [s.by_name() for s in samples] == [{"volt": 1.25}], "curr is not published"

    def test_read_skips_a_demand_with_no_getter(self, fresh):
        inst = FakeInstrument("smu")
        inst.parameters["write_only"] = FakeParameter("write_only", gettable=False)
        device = QCoDeS(fresh("smu"), inst, {"x": QCoDeSSignal(property="write_only")})
        assert list(device.read(1)) == [], "a demand's reading is what was committed, not polled"

    def test_write_signal_sets_the_parameter(self, fresh):
        inst = FakeInstrument("smu")
        device = QCoDeS(fresh("smu"), inst, {"bias": QCoDeSSignal(property="volt")})
        bias = device.signals["bias"]
        device.apply(bias, 1, 3.0)
        assert device.commit(1) is None
        assert inst.parameters["volt"].sets == [3.0]
        assert device.pending[bias] == 3.0, "the rig clears pending, not the driver"

    def test_a_dotted_property_reaches_a_submodule_parameter(self, fresh):
        inst = FakeInstrument("smu")
        inst.source = FakeSubmodule({"voltage": FakeParameter("voltage", "V", value=0.5)})
        device = QCoDeS(
            fresh("smu"), inst, {"v": QCoDeSSignal(property="source.voltage", publish=True)}
        )
        (sample,) = device.read(1)
        assert sample.by_name() == {"v": 0.5}

    def test_bounds_come_from_a_numbers_validator(self):
        assert bounds(FakeParameter("x")) is None
        assert bounds(FakeParameter("x", vals=Numbers())) is None
        assert bounds(FakeParameter("x", vals=Numbers(-10, 10))) == (-10, 10)

    def test_unit_for_overrides_and_falls_back(self):
        assert unit_for("degC", {"degC": "°C"}).symbol == "°C"
        assert unit_for(None).symbol == "1"
        assert unit_for("not-a-unit").symbol == "1"


# region PyMeasure fakes


class FakePyMeasureInstrument:
    """Properties the way ``Instrument.measurement``/``control``/``setting`` build them."""

    def __init__(self):
        self._v = 1.5
        self._sv = 0.0
        self.written: list[float] = []

    @property
    def voltage(self):
        """Reads the measured voltage, in volts."""
        return self._v

    @property
    def current(self):
        """The DC current in amps."""
        return 0.25

    @property
    def source_voltage(self):
        """Control the source voltage in volts (float)."""
        return self._sv

    @source_voltage.setter
    def source_voltage(self, value):
        self._sv = value
        self.written.append(value)

    def _output(self, value):
        self.written.append(value)

    output_enabled = property(
        None, lambda self, v: self._output(v), doc="Set the output on or off."
    )


# endregion


class TestPyMeasure:
    @pytest.mark.parametrize(
        ("doc", "symbol"),
        [
            ("the voltage, in volts.", "V"),
            ("Current in amps", "A"),
            ("temperature in degrees C", "°C"),
            ("flow in mL/min", "mL/min"),
            ("no unit here", "1"),
            (None, "1"),
        ],
    )
    def test_units_come_from_the_docstring(self, doc, symbol):
        assert unit_from_doc(doc).symbol == symbol

    def test_properties_walks_the_class_including_bases(self):
        inst = FakePyMeasureInstrument()
        found = properties(inst)
        assert {"voltage", "current", "source_voltage", "output_enabled"} <= set(found)

    def test_a_gettable_only_property_published_is_rp(self, fresh):
        inst = FakePyMeasureInstrument()
        device = PyMeasure(
            fresh("smu"), inst, {"voltage": PyMeasureSignal(property="voltage", publish=True)}
        )
        assert device.signals["voltage"].access is Access.RP
        assert device.signals["voltage"].quantity.unit.symbol == "V"

    def test_a_settable_property_is_a_demand_and_writable_via_apply_commit(self, fresh):
        inst = FakePyMeasureInstrument()
        device = PyMeasure(fresh("smu"), inst, {"bias": PyMeasureSignal(property="source_voltage")})
        assert device.signals["bias"].role is Role.DEMAND
        assert device.signals["bias"].access is Access.RPW
        bias = device.signals["bias"]
        device.apply(bias, 1, 2.0)
        assert device.commit(1) is None
        assert inst.written == [2.0]
        assert device.pending[bias] == 2.0, "the rig clears pending, not the driver"

    def test_a_setter_only_property_is_a_demand_too(self, fresh):
        inst = FakePyMeasureInstrument()
        device = PyMeasure(
            fresh("out"), inst, {"enable": PyMeasureSignal(property="output_enabled")}
        )
        assert device.signals["enable"].role is Role.DEMAND
        assert device.signals["enable"].access is Access.RPW, "its readback is the committed value"

    def test_read_skips_a_demand_with_no_getter(self, fresh):
        inst = FakePyMeasureInstrument()
        device = PyMeasure(
            fresh("out"), inst, {"enable": PyMeasureSignal(property="output_enabled")}
        )
        assert list(device.read(1)) == [], "write-only: nothing to poll"

    def test_an_unknown_property_is_refused(self, fresh):
        inst = FakePyMeasureInstrument()
        with pytest.raises(ValueError, match="no property 'nope'"):
            PyMeasure(fresh("x"), inst, {"x": PyMeasureSignal(property="nope")})

    def test_publish_without_a_getter_is_refused(self, fresh):
        inst = FakePyMeasureInstrument()
        with pytest.raises(ValueError, match="publish needs a readable property"):
            PyMeasure(
                fresh("x"),
                inst,
                {"enable": PyMeasureSignal(property="output_enabled", publish=True)},
            )

    def test_read_yields_only_due_published_signals(self, fresh):
        inst = FakePyMeasureInstrument()
        device = PyMeasure(
            fresh("smu"),
            inst,
            {
                "voltage": PyMeasureSignal(property="voltage", publish=True),
                "current": PyMeasureSignal(property="current"),
            },
        )
        samples = list(device.read(1))
        assert [s.by_name() for s in samples] == [{"voltage": 1.5}]

    def test_blocking_is_true(self, fresh):
        device = PyMeasure(fresh("x"), FakePyMeasureInstrument(), {})
        assert device.blocking is True


class TestSignalValidation:
    def test_a_signal_needs_extra_forbid(self):
        with pytest.raises(ValidationError):
            QCoDeSSignal(property="volt", nope=True)  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            PyMeasureSignal(property="voltage", nope=True)  # type: ignore[call-arg]


# region A rig file names both drivers


def rig_document(qcodes_name: str, pymeasure_name: str) -> dict:
    return {
        "devices": {
            "smu": {
                "driver": "qcodes",
                "config": {
                    "instrument": f"{__name__}.FakeInstrument",
                    "instrument_name": qcodes_name,
                    "channels": {"volt": {"property": "volt", "publish": True}},
                },
            },
            "k2400": {
                "driver": "pymeasure",
                "config": {
                    "instrument": f"{__name__}.FakePyMeasureInstrumentWithAdapter",
                    "adapter": "GPIB::24",
                    "channels": {"voltage": {"property": "voltage", "publish": True}},
                },
            },
        }
    }


class FakePyMeasureInstrumentWithAdapter(FakePyMeasureInstrument):
    def __init__(self, adapter, **kwargs):
        super().__init__()
        self.adapter = adapter


def test_a_rig_file_names_both_wrapped_drivers(fresh):
    from flyball.runtime.config import RigConfig

    rig = RigConfig.model_validate(rig_document(fresh("smu_name"), fresh("k2400_name"))).build(
        start=False
    )
    assert isinstance(rig.devices["smu"], QCoDeS)
    assert isinstance(rig.devices["k2400"], PyMeasure)
    smu_volt = rig.resolve("smu.volt")
    k2400_voltage = rig.resolve("k2400.voltage")
    assert isinstance(smu_volt, Signal) and isinstance(k2400_voltage, Signal)
    assert smu_volt.access is Access.RPW, "volt is settable too"
    assert k2400_voltage.access is Access.RP


# endregion
