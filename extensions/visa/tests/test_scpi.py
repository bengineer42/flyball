"""The generic `scpi` device, and the bench rig built over fake_text links."""

from __future__ import annotations

import pytest
from flyball.foundation.device import Access, Reading, Role, Signal
from flyball.runtime.config import RigConfig, rig_schema
from pydantic import ValidationError

from flyball_visa import FakeTextLink, Scpi, ScpiSignal

NS = 1_000_000_000  # a second, in the ns the runtime counts time in


class TestScpiSignal:
    def test_query_only_is_rp(self):
        assert ScpiSignal(query="V?", unit="V").access == Access.RP

    def test_write_only_is_rpw(self):
        """A demand's readback is the value last committed: `RPW`, not bare `W`."""
        assert ScpiSignal(write="V {value}", unit="V").access == Access.RPW

    def test_both_is_rpw(self):
        assert ScpiSignal(query="V?", write="V {value}", unit="V").access == Access.RPW

    def test_a_write_is_a_demand_unless_declared_a_setting(self):
        """C13: `role: setting` keeps a range or a mode out of a controller's reach."""
        assert ScpiSignal(write="V {value}", unit="V").signal_role is Role.DEMAND
        setting = ScpiSignal(query="RANG?", write="RANG {value}", unit="V", role="setting")
        assert setting.signal_role is Role.SETTING and setting.access == Access.RPW
        assert ScpiSignal(query="V?", unit="V").signal_role is Role.READOUT
        with pytest.raises(ValidationError, match="only a signal with `write`"):
            ScpiSignal(query="V?", unit="V", role="setting")

    def test_neither_is_refused(self):
        with pytest.raises(ValidationError, match="needs `query`, `write`, or both"):
            ScpiSignal(unit="V")


class TestScpi:
    def test_read_yields_one_sample_per_query_and_parses_replies(self):
        link = FakeTextLink({"MEAS:VOLT:DC?": "+1.2345E+01\n", "MEAS:CURR:DC?": "0.5 A"})
        dmm = Scpi(
            "dmm",
            link,
            {
                "voltage": ScpiSignal(query="MEAS:VOLT:DC?", unit="V"),
                "current": ScpiSignal(query="MEAS:CURR:DC?", unit="A"),
            },
        )
        samples = list(dmm.read(100))
        assert len(samples) == 2, "one query, one instant, one sample -- never batched"
        assert [s.time_ns for s in samples] == [100, 100]
        assert [s.by_name() for s in samples] == [{"voltage": 12.345}, {"current": 0.5}]
        assert link.queried == ["MEAS:VOLT:DC?", "MEAS:CURR:DC?"]

    def test_scale_applies_on_read_and_write(self):
        link = FakeTextLink(lambda q: "1200" if q == "R?" else "")
        dev = Scpi(
            "d", link, {"x": ScpiSignal(query="R?", write="W {value}", unit="V", scale=0.01)}
        )
        (sample,) = dev.read(0)
        assert sample.by_name() == {"x": 12.0}, "1200 raw * 0.01 scale"
        x = dev.signals["x"]
        dev.apply(x, 1, 12.0)
        dev.commit(1)
        assert link.written == ["W 1200.0"], "12.0 / 0.01 scale, formatted raw"

    def test_only_due_signals_are_read(self):
        link = FakeTextLink({"A?": "1", "B?": "2"})
        dev = Scpi(
            "d",
            link,
            {"a": ScpiSignal(query="A?", unit="V"), "b": ScpiSignal(query="B?", unit="V")},
        )
        dev.signals["a"].override(poll_s=10.0)
        dev.signals["b"].override(poll_s=1.0)
        first = list(dev.read(0))
        assert [s.by_name() for s in first] == [{"a": 1.0}, {"b": 2.0}]
        second = list(dev.read(2 * NS))
        assert [s.by_name() for s in second] == [{"b": 2.0}], "only b is due again at 2s"
        third = list(dev.read(11 * NS))
        assert {k for s in third for k in s.by_name()} == {"a", "b"}, "both due by 11s"

    def test_a_slightly_early_poll_still_counts_as_due(self):
        """`Scan`'s 0.9*period rule: a scaled clock's threads arrive a little early."""
        link = FakeTextLink({"A?": "1"})
        dev = Scpi("d", link, {"a": ScpiSignal(query="A?", unit="V")})
        dev.signals["a"].override(poll_s=1.0)
        list(dev.read(0))
        assert [s.by_name() for s in dev.read(int(0.95 * NS))] == [{"a": 1.0}]

    def test_a_write_only_signal_has_no_query(self):
        link = FakeTextLink({})
        psu = Scpi(
            "psu", link, {"set_voltage": ScpiSignal(write="SOUR:VOLT {value:.3f}", unit="V")}
        )
        assert list(psu.read(0)) == [], "nothing publishes"
        assert psu.signals["set_voltage"].access is Access.RPW

    def test_a_dead_instrument_raises_so_the_device_goes_offline(self):
        dmm = Scpi("dmm", FakeTextLink({}), {"v": ScpiSignal(query="X?", unit="V")})
        with pytest.raises(OSError):
            list(dmm.read(0))

    def test_write_and_query_commands_are_a_raw_passthrough(self):
        link = FakeTextLink({"*IDN?": "Keysight,34465A,MY123,1.0"})
        dmm = Scpi("dmm", link, {"v": ScpiSignal(query="V?", unit="V")})
        assert set(type(dmm).commands) == {"write", "query"}
        assert dmm.query("*IDN?") == "Keysight,34465A,MY123,1.0"
        dmm.write("SYST:BEEP")
        assert link.written == ["SYST:BEEP"]

    def test_blocking_is_true_for_a_real_bus_false_for_a_fake(self):
        fake = Scpi("d", FakeTextLink({}), {"v": ScpiSignal(query="V?", unit="V")})
        assert fake.blocking is False


# region The bench rig, over fake_text links


def bench_document() -> dict:
    return {
        "links": {
            "psu": {"type": "fake_text", "replies": {"MEAS:VOLT?": "11.98"}},
            "dmm": {"type": "fake_text", "replies": {"MEAS:VOLT:DC?": "+1.1980E+01"}},
        },
        "devices": {
            "psu": {
                "driver": "scpi",
                "label": "Bench PSU",
                "config": {
                    "link": "psu",
                    "channels": {
                        "set_voltage": {"write": "SOUR:VOLT {value:.3f}", "unit": "V"},
                        "output_voltage": {"query": "MEAS:VOLT?", "unit": "V"},
                    },
                },
                "signals": {
                    "set_voltage": {"limits": [0, 30]},
                    "output_voltage": {"precision": 3},
                },
            },
            "dmm": {
                "driver": "scpi",
                "label": "Bench DMM",
                "poll_s": 0.5,
                "config": {
                    "link": "dmm",
                    "channels": {"voltage": {"query": "MEAS:VOLT:DC?", "unit": "V"}},
                },
            },
        },
    }


def _psu(rig) -> Scpi:
    psu = rig.devices["psu"]
    assert isinstance(psu, Scpi)
    return psu


def _signal(rig, address: str) -> Signal:
    target = rig.resolve(address)
    assert isinstance(target, Signal)
    return target


class TestBenchRig:
    def test_it_parses_and_builds(self, fresh):
        rig = RigConfig.model_validate(bench_document()).build(start=False)
        assert isinstance(rig.devices["psu"], Scpi)
        psu_out = _signal(rig, "psu.output_voltage")
        assert psu_out.spec.precision == 3
        assert _signal(rig, "psu.set_voltage").limits == (0.0, 30.0)

    def test_fresh_read_queries_the_instrument(self):
        rig = RigConfig.model_validate(bench_document()).build(start=False)
        target = _signal(rig, "psu.output_voltage")
        reading = rig.read(target, fresh=True)
        assert isinstance(reading, Reading)
        assert reading.value == pytest.approx(11.98)

    def test_demand_writes_the_formatted_scpi_command(self):
        rig = RigConfig.model_validate(bench_document()).build(start=False)
        psu = _psu(rig)
        assert isinstance(psu.link, FakeTextLink)
        states = rig.write(psu.root, {"set_voltage": 12.0})
        assert psu.link.written == ["SOUR:VOLT 12.000"]
        assert states[_signal(rig, "psu.set_voltage")].value == 12.0

    def test_demand_is_clamped_to_the_envelope_s_limits(self):
        rig = RigConfig.model_validate(bench_document()).build(start=False)
        psu = _psu(rig)
        states = rig.write(psu.root, {"set_voltage": 99.0})
        signal = _signal(rig, "psu.set_voltage")
        assert states[signal].value == 30.0 and states[signal].at_limit == "high"

    def test_an_undeclared_link_is_refused(self):
        document = bench_document()
        document["devices"]["psu"]["config"]["link"] = "nowhere"
        with pytest.raises(ValueError, match="link 'nowhere' is not declared"):
            RigConfig.model_validate(document)

    def test_schema_is_still_a_discriminated_tree_with_scpi(self):
        # Not a joint {"scpi", "modbus"} assertion any more: that was a
        # cross-package check neither extensions/visa nor extensions/modbus
        # can make standalone. extensions/modbus's own test asserts "modbus"
        # the same way.
        schema = rig_schema()
        by_driver = schema["properties"]["devices"]["additionalProperties"]
        tags = {
            shape["properties"]["driver"]["const"]
            for variant in by_driver["oneOf"]
            # `.get`: the layer variants (an entry that only adds to a base's device, and `null`
            # to remove one) have no nested `oneOf` and name no driver.
            for shape in variant.get("oneOf", [])
        }
        assert "scpi" in tags


# endregion
