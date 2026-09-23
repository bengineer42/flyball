"""The generic `modbus` device, and a small rig built over a fake_registers link."""

from __future__ import annotations

import pytest
from flyball.foundation.device import Access
from flyball.runtime.config import RigConfig, rig_schema
from pydantic import ValidationError

from flyball_modbus import FakeRegisterLink, Modbus, ModbusRegister
from flyball_modbus._links import ModbusLink

NS = 1_000_000_000  # a second, in the ns the runtime counts time in


class FakePymodbusClient:
    """Mimics pymodbus's client interface (>=3.10: `device_id=`, not `slave=`)."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def connect(self) -> None:
        pass

    def read_holding_registers(self, address: int, count: int, device_id: int):
        self.calls.append(("read", address, count, device_id))
        return type("Result", (), {"isError": lambda self: False, "registers": [42] * count})()

    def write_registers(self, address: int, values: list[int], device_id: int):
        self.calls.append(("write", address, values, device_id))
        return type("Result", (), {"isError": lambda self: False})()


class TestModbusLink:
    def test_read_registers_passes_device_id_not_slave(self):
        client = FakePymodbusClient()
        link = ModbusLink(client)
        assert link.read_registers(100, 2, unit=7) == [42, 42]
        assert client.calls == [("read", 100, 2, 7)]

    def test_write_registers_passes_device_id_not_slave(self):
        client = FakePymodbusClient()
        link = ModbusLink(client)
        link.write_registers(100, [1, 2], unit=7)
        assert client.calls == [("write", 100, [1, 2], 7)]


class TestModbusRegister:
    def test_a_holding_register_defaults_to_read_publish(self):
        assert ModbusRegister(address=1, unit="°C").access == Access.RP

    def test_write_true_adds_w(self):
        assert ModbusRegister(address=1, unit="°C", write=True).access == Access.RPW

    def test_an_input_register_cannot_be_written(self):
        with pytest.raises(ValidationError, match="input register cannot be written"):
            ModbusRegister(address=1, kind="input", unit="°C", write=True)


class TestModbus:
    def test_read_scales_a_register_and_decode_write_round_trips(self):
        link = FakeRegisterLink({100: 215})
        bath = Modbus(
            "bath", link, {"temperature": ModbusRegister(address=100, unit="°C", scale=0.1)}
        )
        (sample,) = bath.read(1)
        assert sample.by_name() == {"temperature": pytest.approx(21.5)}

    def test_write_quantises_through_scale(self):
        link = FakeRegisterLink({200: 0})
        valve = Modbus(
            "valve",
            link,
            {"setpoint": ModbusRegister(address=200, unit="°C", scale=0.1, write=True)},
        )
        setpoint = valve.signals["setpoint"]
        valve.apply(setpoint, 1, 25.04)
        valve.commit(1)
        assert link.registers[200] == 250
        assert setpoint.value == pytest.approx(25.0), "the quantised word actually set"

    def test_only_due_signals_are_read(self):
        link = FakeRegisterLink({1: 10, 2: 20})
        dev = Modbus(
            "d",
            link,
            {"a": ModbusRegister(address=1, unit="1"), "b": ModbusRegister(address=2, unit="1")},
        )
        dev.signals["a"].override(poll_s=10.0)
        dev.signals["b"].override(poll_s=1.0)
        assert [s.by_name() for s in dev.read(0)] == [{"a": 10.0}, {"b": 20.0}]
        assert [s.by_name() for s in dev.read(2 * NS)] == [{"b": 20.0}]

    def test_a_slightly_early_poll_still_counts_as_due(self):
        """`Scan`'s 0.9*period rule: a scaled clock's threads arrive a little early."""
        link = FakeRegisterLink({1: 10})
        dev = Modbus("d", link, {"a": ModbusRegister(address=1, unit="1")})
        dev.signals["a"].override(poll_s=1.0)
        list(dev.read(0))
        assert [s.by_name() for s in dev.read(int(0.95 * NS))] == [{"a": 10.0}]

    def test_blocking_is_true_for_a_real_bus_false_for_a_fake(self):
        dev = Modbus("d", FakeRegisterLink({}), {"a": ModbusRegister(address=1, unit="1")})
        assert dev.blocking is False


# region A small rig, over a fake_registers link


def bench_document() -> dict:
    return {
        "links": {"chiller": {"tag": "fake_registers", "registers": {100: 215}}},
        "devices": {
            "chiller": {
                "driver": "modbus",
                "label": "Bench chiller",
                "config": {
                    "link": "chiller",
                    "registers": {
                        "temperature": {"address": 100, "unit": "°C", "scale": 0.1},
                    },
                },
            },
        },
    }


class TestBenchRig:
    def test_it_parses_and_builds(self):
        rig = RigConfig.model_validate(bench_document()).build(start=False)
        assert isinstance(rig.devices["chiller"], Modbus)

    def test_an_undeclared_link_is_refused(self):
        document = bench_document()
        document["devices"]["chiller"]["config"]["link"] = "nowhere"
        with pytest.raises(ValueError, match="link 'nowhere' is not declared"):
            RigConfig.model_validate(document)

    def test_schema_is_still_a_discriminated_tree_with_modbus(self):
        # Not a joint {"scpi", "modbus"} assertion any more: that was a
        # cross-package check neither extensions/modbus nor extensions/visa
        # can make standalone. extensions/visa's own test asserts "scpi" the
        # same way.
        schema = rig_schema()
        by_driver = schema["properties"]["devices"]["additionalProperties"]
        tags = {
            shape["properties"]["driver"]["const"]
            for variant in by_driver["oneOf"]
            # `.get`: the layer variants (an entry that only adds to a base's device, and `null`
            # to remove one) have no nested `oneOf` and name no driver.
            for shape in variant.get("oneOf", [])
        }
        assert "modbus" in tags


# endregion
