"""The QCoDeS device, against a fake that mimics the library's interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from flyball.foundation.device import Access, Role
from pydantic import ValidationError

from flyball_qcodes import QCoDeS, QCoDeSSignal, bounds, unit_for

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

    def test_a_settable_parameter_declared_a_setting_is_one(self, fresh):
        """C13: `role: setting` keeps a range or a mode out of a controller's reach."""
        inst = FakeInstrument("smu")
        device = QCoDeS(fresh("smu"), inst, {"v": QCoDeSSignal(property="volt", role="setting")})
        assert device.signals["v"].role is Role.SETTING
        assert device.signals["v"].access is Access.RPW
        with pytest.raises(ValueError, match="only a settable parameter"):
            QCoDeS(fresh("smu"), inst, {"t": QCoDeSSignal(property="temp", role="setting")})

    def test_a_read_only_parameter_is_an_output(self, fresh):
        inst = FakeInstrument("smu")
        device = QCoDeS(fresh("smu"), inst, {"temp": QCoDeSSignal(property="temp")})
        assert device.signals["temp"].role is Role.READOUT
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

    def test_a_slightly_early_poll_still_counts_as_due(self, fresh):
        """`Scan`'s 0.9*period rule: a scaled clock's threads arrive a little early."""
        inst = FakeInstrument("smu")
        device = QCoDeS(fresh("smu"), inst, {"volt": QCoDeSSignal(property="volt", publish=True)})
        device.signals["volt"].override(poll_s=1.0)
        list(device.read(0))
        samples = list(device.read(int(0.95e9)))
        assert [s.by_name() for s in samples] == [{"volt": 1.25}]

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
        assert device.staged[bias] == 3.0, "the rig clears staged, not the driver"

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


def test_a_signal_needs_extra_forbid():
    with pytest.raises(ValidationError):
        QCoDeSSignal(property="volt", nope=True)  # type: ignore[call-arg]


def test_a_rig_file_names_the_driver(fresh):
    from flyball.runtime.config import RigConfig

    qcodes_name = fresh("smu_name")
    document = {
        "devices": {
            "smu": {
                "driver": "qcodes",
                "config": {
                    "instrument": f"{__name__}.FakeInstrument",
                    "instrument_name": qcodes_name,
                    "channels": {"volt": {"property": "volt", "publish": True}},
                },
            }
        }
    }
    rig = RigConfig.model_validate(document).build(start=False)
    assert isinstance(rig.devices["smu"], QCoDeS)
    assert rig.resolve("smu.volt").access is Access.RPW, "volt is settable too"
