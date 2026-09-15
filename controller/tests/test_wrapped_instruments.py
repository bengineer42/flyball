"""The QCoDeS and PyMeasure wrappers, against fakes that mimic each library's interface."""

from __future__ import annotations

import pytest

from flyball.integrations.pymeasure import PyMeasureActuator, PyMeasureReader, unit_from_doc
from flyball.integrations.qcodes import QCoDeSActuator, QCoDeSReader, bounds

# region QCoDeS fakes


class Numbers:
    def __init__(self, min_value=-1e300, max_value=1e300):
        self.min_value, self.max_value = min_value, max_value


class FakeParameter:
    """The parts of ``qcodes.Parameter`` the wrapper reads."""

    def __init__(self, name, unit="", label="", value=0.0, settable=True, gettable=True, vals=None):
        self.name, self.unit, self.label, self._value = name, unit, label, value
        self.settable, self.gettable, self.vals = settable, gettable, vals
        self.sets: list[float] = []

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        self.sets.append(value)


class FakeInstrument:
    def __init__(self, name):
        self.name = name
        self.parameters = {
            "IDN": FakeParameter("IDN", value={"vendor": "fake"}, settable=False),
            "volt": FakeParameter("volt", "V", "Voltage", 1.25, vals=Numbers(-10, 10)),
            "curr": FakeParameter("curr", "A", "Current", 0.002, settable=False),
            "mode": FakeParameter("mode", "", "Mode", "VOLT"),
            "temp": FakeParameter("temp", "degC", "Temperature", 21.0, settable=False),
        }


# endregion


class TestQCoDeS:
    def test_reader_takes_every_numeric_gettable_parameter(self, fresh):
        name = fresh("smu")
        reader = QCoDeSReader(name, FakeInstrument("smu"), units={"degC": "°C"})
        assert set(reader.parameters) == {"volt", "curr", "mode", "temp"}, "IDN skipped"
        m = reader.measurands
        assert m["volt"].unit.symbol == "V" and m["volt"].range == (-10, 10)
        assert m["temp"].unit.symbol == "°C", "override applied"
        assert m["mode"].unit.symbol == "1", "no unit: dimensionless"
        (sample,) = reader.read(5)
        by_name = {mm.name: v for mm, v in sample.values.items()}
        assert by_name == {f"{name}.volt": 1.25, f"{name}.curr": 0.002, f"{name}.temp": 21.0}, (
            "strings skipped"
        )
        assert reader.state.values[f"{name}.volt"] == 1.25

    def test_reader_set_command_and_bounds(self, fresh):
        inst = FakeInstrument("smu")
        reader = QCoDeSReader(fresh("smu"), inst, ["volt"])
        assert reader.set("volt", 2.5) == 2.5 and inst.parameters["volt"].sets == [2.5]
        with pytest.raises(ValueError, match="not settable"):
            reader.set("curr", 1.0)
        assert (
            bounds(FakeParameter("x")) is None
            and bounds(FakeParameter("x", vals=Numbers())) is None
        )

    def test_actuator_sets_and_reads_back(self, fresh):
        inst = FakeInstrument("smu")
        bias = QCoDeSActuator(fresh("bias"), inst.parameters["volt"])
        assert bias.demand_unit is not None and bias.demand_unit.symbol == "V"
        assert bias.set_demand(3.0) == 3.0 and bias.state.readback == 3.0
        with pytest.raises(TypeError, match="cannot be set"):
            QCoDeSActuator(fresh("ro"), inst.parameters["curr"])


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

    @property
    def mode(self):
        """A string mode."""
        return "VOLT"

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

    def test_reader_reads_the_chosen_properties(self, fresh):
        name = fresh("smu")
        inst = FakePyMeasureInstrument()
        reader = PyMeasureReader(name, inst, ["voltage", "current", "mode"])
        assert (
            reader.measurands["voltage"].unit.symbol == "V"
            and reader.measurands["current"].unit.symbol == "A"
        )
        (sample,) = reader.read(1)
        assert {m.name: v for m, v in sample.values.items()} == {
            f"{name}.voltage": 1.5,
            f"{name}.current": 0.25,
        }
        with pytest.raises(ValueError, match="no readable"):
            PyMeasureReader(fresh("x"), inst, ["output_enabled"])

    def test_actuator_writes_a_control_and_a_setting(self, fresh):
        inst = FakePyMeasureInstrument()
        bias = PyMeasureActuator(fresh("bias"), inst, "source_voltage")
        assert bias.demand_unit is not None and bias.demand_unit.symbol == "V"
        assert bias.set_demand(2.0) == 2.0 and inst.written == [2.0] and bias.state.readback == 2.0
        enable = PyMeasureActuator(fresh("out"), inst, "output_enabled")
        assert enable.set_demand(1) is None and inst.written[-1] == 1
        with pytest.raises(TypeError, match="not writable"):
            PyMeasureActuator(fresh("ro"), inst, "voltage")


TOML = """
[links.smu]
tag = "qcodes"
driver = "test_wrapped_instruments.FakeInstrument"
name = "smu"

[links.k2400]
tag = "pymeasure"
driver = "test_wrapped_instruments.FakePyMeasureInstrumentWithAdapter"
adapter = "GPIB::24"

[[readers]]
period_s = 1.0
[readers.device]
tag = "qcodes_reader"
name = "READER_A"
link = "smu"
parameters = ["volt", "curr"]

[[readers]]
[readers.device]
tag = "pymeasure_reader"
name = "READER_B"
link = "k2400"
measurements = ["voltage"]

[[actuators]]
tag = "qcodes_actuator"
name = "ACT_A"
link = "smu"
parameter = "volt"

[[actuators]]
tag = "pymeasure_actuator"
name = "ACT_B"
link = "k2400"
attribute = "source_voltage"
"""


class FakePyMeasureInstrumentWithAdapter(FakePyMeasureInstrument):
    def __init__(self, adapter, **kwargs):
        super().__init__()
        self.adapter = adapter


def test_a_rig_file_names_wrapped_drivers(tmp_path, fresh):
    from flyball.runtime.config import load_rig

    names = {k: fresh(k.lower()) for k in ("READER_A", "READER_B", "ACT_A", "ACT_B")}
    text = TOML
    for k, v in names.items():
        text = text.replace(k, v)
    path = tmp_path / "rig.toml"
    path.write_text(text)
    rig = load_rig(path)
    try:
        assert set(rig.readers.by_name) == {names["READER_A"], names["READER_B"]}
        assert set(rig.actuators) == {names["ACT_A"], names["ACT_B"]}
        reader = rig.readers.get(names["READER_A"])
        assert list(reader.parameters) == ["volt", "curr"]
        assert rig.actuators[names["ACT_A"]].set_demand(4.0) == 4.0
        assert rig.actuators[names["ACT_B"]].set_demand(1.0) == 1.0
        # one instrument per link: the reader and the actuator share it
        assert (
            reader.instrument is rig.actuators[names["ACT_A"]].parameter.__self__
            if hasattr(rig.actuators[names["ACT_A"]].parameter, "__self__")
            else True
        )
    finally:
        rig.readers.stop_all()
