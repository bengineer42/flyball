"""B24: a fake link's `blocking` is a config option now, not decided by `isinstance`.

A `fake_registers` link with `blocking: true` must make its `modbus` device
`blocking = True` too, so the Writer thread and the `write_failed` path
actually run against it -- previously `isinstance(link, FakeRegisterLink)`
made every fake non-blocking regardless of configuration, so a sim or a
test could never exercise that path.

B26: the explicit `ModbusTcpClient` pymodbus builds gets a `timeout_s`
(default 3 s), instead of taking pymodbus's own, unstated default.
"""

from __future__ import annotations

import time

import pytest
from flyball.runtime.config import RigConfig

from flyball_modbus import FakeRegisterLink, Modbus, ModbusRegister
from flyball_modbus._links import ModbusLink


def _wait_until(condition, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.005)


class TestFakeBlockingOption:
    def test_default_fake_is_not_blocking(self):
        link = FakeRegisterLink({100: 0})
        dev = Modbus("d", link, {"a": ModbusRegister(address=100, unit="1")})
        assert dev.blocking is False

    def test_fake_configured_blocking_makes_the_device_blocking(self):
        link = FakeRegisterLink({100: 0}, blocking=True)
        dev = Modbus("d", link, {"a": ModbusRegister(address=100, unit="1")})
        assert dev.blocking is True


class TestFakeBlockingGoesThroughTheWriter:
    def _rig_document(self) -> dict:
        return {
            "links": {
                "chiller": {
                    "type": "fake_registers",
                    "registers": {100: 0},
                    "blocking": True,
                }
            },
            "devices": {
                "chiller": {
                    "driver": "modbus",
                    "link": "chiller",
                    "registers": {
                        "setpoint": {
                            "address": 100,
                            "unit": "°C",
                            "scale": 0.1,
                            "write": True,
                        }
                    },
                }
            },
        }

    def test_a_blocking_fake_commits_on_the_writer_thread_not_the_delivery(self):
        rig = RigConfig.model_validate(self._rig_document()).build(start=False)
        chiller = rig.devices["chiller"]
        assert chiller.blocking is True

        assert rig.write(chiller.root, {"setpoint": 25.0}) == {}, "queued, not done inline"
        assert rig._writers, "a writer thread was started for the blocking fake"

        _wait_until(lambda: chiller.link.writes != [])
        assert chiller.link.registers[100] == 250
        rig.close()


class TestModbusTcpTimeout:
    def test_tcp_config_default_timeout_is_three_seconds(self):
        from flyball_modbus._links import ModbusTcpConfig

        assert ModbusTcpConfig(host="10.0.0.1").timeout_s == 3.0

    def test_tcp_client_is_built_with_the_configured_timeout(self, monkeypatch):
        # pymodbus is the `modbus` extra; without it there is no client class to patch.
        pymodbus_client = pytest.importorskip("pymodbus.client")
        captured: dict = {}

        class FakePymodbusClient:
            def __init__(self, host, *, port, timeout):
                captured["host"] = host
                captured["port"] = port
                captured["timeout"] = timeout

            def connect(self):
                pass

        monkeypatch.setattr(pymodbus_client, "ModbusTcpClient", FakePymodbusClient)
        ModbusLink.tcp("10.0.0.1", port=502, timeout_s=7.5)
        assert captured == {"host": "10.0.0.1", "port": 502, "timeout": 7.5}
