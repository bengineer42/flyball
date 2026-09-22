"""The PyMeasure device, against a fake that mimics the library's interface."""

from __future__ import annotations

import pytest
from flyball.foundation.device import Access, Role
from pydantic import ValidationError

from flyball_pymeasure import PyMeasure, PyMeasureSignal, properties, unit_from_doc

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


def test_a_signal_needs_extra_forbid():
    with pytest.raises(ValidationError):
        PyMeasureSignal(property="voltage", nope=True)  # type: ignore[call-arg]


class FakePyMeasureInstrumentWithAdapter(FakePyMeasureInstrument):
    def __init__(self, adapter, **kwargs):
        super().__init__()
        self.adapter = adapter


def test_a_rig_file_names_the_driver(fresh):
    from flyball.runtime.config import RigConfig

    document = {
        "devices": {
            "k2400": {
                "driver": "pymeasure",
                "config": {
                    "instrument": f"{__name__}.FakePyMeasureInstrumentWithAdapter",
                    "adapter": "GPIB::24",
                    "channels": {"voltage": {"property": "voltage", "publish": True}},
                },
            }
        }
    }
    rig = RigConfig.model_validate(document).build(start=False)
    assert isinstance(rig.devices["k2400"], PyMeasure)
    assert rig.resolve("k2400.voltage").access is Access.RP
