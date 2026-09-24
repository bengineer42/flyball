"""The generic `modbus` device, and a small rig built over a fake_registers link."""

from __future__ import annotations

import pytest
from flyball.foundation.device import Access, Role
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
        self.calls.append(("read_holding_registers", address, count, device_id))
        return type("Result", (), {"isError": lambda self: False, "registers": [42] * count})()

    def read_input_registers(self, address: int, count: int, device_id: int):
        self.calls.append(("read_input_registers", address, count, device_id))
        return type("Result", (), {"isError": lambda self: False, "registers": [42] * count})()

    def read_coils(self, address: int, count: int, device_id: int):
        self.calls.append(("read_coils", address, count, device_id))
        return type("Result", (), {"isError": lambda self: False, "bits": [True] * 8})()

    def read_discrete_inputs(self, address: int, count: int, device_id: int):
        self.calls.append(("read_discrete_inputs", address, count, device_id))
        return type("Result", (), {"isError": lambda self: False, "bits": [False] * 8})()

    def write_registers(self, address: int, values: list[int], device_id: int):
        self.calls.append(("write_registers", address, values, device_id))
        return type("Result", (), {"isError": lambda self: False})()

    def write_coil(self, address: int, value: bool, device_id: int):
        self.calls.append(("write_coil", address, value, device_id))
        return type("Result", (), {"isError": lambda self: False})()


class TestModbusLink:
    def test_read_registers_passes_device_id_not_slave(self):
        client = FakePymodbusClient()
        link = ModbusLink(client)
        assert link.read_registers(100, 2, unit=7) == [42, 42]
        assert client.calls == [("read_holding_registers", 100, 2, 7)]

    def test_write_registers_passes_device_id_not_slave(self):
        client = FakePymodbusClient()
        link = ModbusLink(client)
        link.write_registers(100, [1, 2], unit=7)
        assert client.calls == [("write_registers", 100, [1, 2], 7)]

    def test_holding_kind_uses_fc03_and_fc16(self):
        client = FakePymodbusClient()
        link = ModbusLink(client)
        link.read_registers(100, 1, unit=1, kind="holding")
        link.write_registers(100, [1], unit=1, kind="holding")
        assert [call[0] for call in client.calls] == ["read_holding_registers", "write_registers"]

    def test_input_kind_uses_fc04_and_is_read_only(self):
        client = FakePymodbusClient()
        link = ModbusLink(client)
        assert link.read_registers(100, 1, unit=1, kind="input") == [42]
        assert client.calls == [("read_input_registers", 100, 1, 1)]
        with pytest.raises(ValueError, match="read-only"):
            link.write_registers(100, [1], unit=1, kind="input")

    def test_discrete_kind_uses_fc02_and_is_read_only(self):
        client = FakePymodbusClient()
        link = ModbusLink(client)
        assert link.read_registers(100, 1, unit=1, kind="discrete") == [0]
        assert client.calls == [("read_discrete_inputs", 100, 1, 1)]
        with pytest.raises(ValueError, match="read-only"):
            link.write_registers(100, [1], unit=1, kind="discrete")

    def test_coil_kind_uses_fc01_and_fc05(self):
        client = FakePymodbusClient()
        link = ModbusLink(client)
        assert link.read_registers(100, 1, unit=1, kind="coil") == [1]
        link.write_registers(100, [1], unit=1, kind="coil")
        assert [call[0] for call in client.calls] == ["read_coils", "write_coil"]
        assert client.calls[1] == ("write_coil", 100, True, 1)


class TestModbusRegister:
    def test_a_holding_register_defaults_to_read_publish(self):
        assert ModbusRegister(address=1, unit="°C").access == Access.RP

    def test_write_true_adds_w(self):
        assert ModbusRegister(address=1, unit="°C", write=True).access == Access.RPW

    def test_a_writable_register_is_a_demand_unless_declared_a_setting(self):
        """C13: `role: setting` keeps a configuration register out of a controller's reach."""
        assert ModbusRegister(address=1, unit="°C", write=True).signal_role is Role.DEMAND
        setting = ModbusRegister(address=1, unit="Hz", write=True, role="setting")
        assert setting.signal_role is Role.SETTING and setting.access == Access.RPW
        assert ModbusRegister(address=1, unit="°C").signal_role is Role.READOUT
        with pytest.raises(ValidationError, match="only a writable register"):
            ModbusRegister(address=1, unit="Hz", role="setting")

    def test_an_input_register_cannot_be_written(self):
        with pytest.raises(ValidationError, match="input register cannot be written"):
            ModbusRegister(address=1, kind="input", unit="°C", write=True)

    def test_a_discrete_register_cannot_be_written(self):
        with pytest.raises(ValidationError, match="discrete register cannot be written"):
            ModbusRegister(address=1, kind="discrete", unit="1", write=True)

    def test_a_coil_or_discrete_register_takes_no_scale(self):
        with pytest.raises(ValidationError, match="no scale"):
            ModbusRegister(address=1, kind="coil", unit="1", scale=2.0)
        with pytest.raises(ValidationError, match="no scale"):
            ModbusRegister(address=1, kind="discrete", unit="1", scale=2.0)

    def test_coil_decodes_and_encodes_as_a_plain_bit(self):
        reg = ModbusRegister(address=1, kind="coil", unit="1")
        assert reg.decode([1]) == 1.0
        assert reg.decode([0]) == 0.0
        assert reg.encode(1.0) == [1]
        assert reg.encode(0.0) == [0]


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
        dev.signals["a"].set_meta(poll_s=10.0)
        dev.signals["b"].set_meta(poll_s=1.0)
        assert [s.by_name() for s in dev.read(0)] == [{"a": 10.0}, {"b": 20.0}]
        assert [s.by_name() for s in dev.read(2 * NS)] == [{"b": 20.0}]

    def test_a_slightly_early_poll_still_counts_as_due(self):
        """`Scan`'s 0.9*period rule: a scaled clock's threads arrive a little early."""
        link = FakeRegisterLink({1: 10})
        dev = Modbus("d", link, {"a": ModbusRegister(address=1, unit="1")})
        dev.signals["a"].set_meta(poll_s=1.0)
        list(dev.read(0))
        assert [s.by_name() for s in dev.read(int(0.95 * NS))] == [{"a": 10.0}]

    def test_blocking_is_true_for_a_real_bus_false_for_a_fake(self):
        dev = Modbus("d", FakeRegisterLink({}), {"a": ModbusRegister(address=1, unit="1")})
        assert dev.blocking is False

    def test_read_and_write_pass_the_registers_own_kind_to_the_link(self):
        link = FakeRegisterLink({100: 7})
        dev = Modbus(
            "d",
            link,
            {
                "holding": ModbusRegister(address=100, unit="1"),
                "input": ModbusRegister(address=200, kind="input", unit="1"),
                "coil": ModbusRegister(address=300, kind="coil", unit="1", write=True),
            },
        )
        list(dev.read(0))
        assert {r[0] for r in link.reads} == {"holding", "input", "coil"}

        coil = dev.signals["coil"]
        dev.apply(coil, 1, 1.0)
        dev.commit(1)
        assert link.writes == [("coil", 300, [1])]

    def test_input_kind_is_read_only_on_the_signal_spec(self):
        dev = Modbus(
            "d", FakeRegisterLink({}), {"a": ModbusRegister(address=1, kind="input", unit="1")}
        )
        assert dev.signals["a"].access == Access.RP


# region A small rig, over a fake_registers link


def bench_document() -> dict:
    return {
        "links": {"chiller": {"type": "fake_registers", "registers": {100: 215}}},
        "devices": {
            "chiller": {
                "driver": "modbus",
                "label": "Bench chiller",
                "link": "chiller",
                "registers": {
                    "temperature": {"address": 100, "unit": "°C", "scale": 0.1},
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
        document["devices"]["chiller"]["link"] = "nowhere"
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
            for shape in by_driver["oneOf"]
            # The layer variants (an entry that only adds to a base's device, and `null` to
            # remove one) name no driver.
            if "driver" in shape.get("properties", {})
        }
        assert "modbus" in tags


# endregion
