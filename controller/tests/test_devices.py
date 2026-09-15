"""Generic SCPI and Modbus devices over fake links, and a rig built from a file."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from flyball.core.files import load_document, loads
from flyball.devices import (
    ModbusActuator,
    ModbusReader,
    Register,
    ScpiActuator,
    ScpiMeasurand,
    ScpiReader,
)
from flyball.hardware.links import FakeRegisterLink, FakeTextLink
from flyball.runtime.config import RigConfig, load_rig, rig_schema


class TestScpi:
    def test_reader_polls_every_query_and_parses_replies(self, fresh):
        link = FakeTextLink({"MEAS:VOLT:DC?": "+1.2345E+01\n", "MEAS:CURR:DC?": "0.5 A"})
        dmm = ScpiReader(
            fresh("dmm"),
            link,
            {
                "voltage": ScpiMeasurand(
                    query="MEAS:VOLT:DC?", unit="V", range=(0, 30), precision=3
                ),
                "current": ScpiMeasurand(query="MEAS:CURR:DC?", unit="A"),
            },
        )
        (sample,) = dmm.read(100)
        by_name = {m.name: v for m, v in sample.values.items()}
        assert by_name == {"voltage": 12.345, "current": 0.5}
        assert dmm.measurands["voltage"].unit.symbol == "V"
        assert dmm.measurands["voltage"].range == (0, 30)
        assert dmm.state.values == {"voltage": 12.345, "current": 0.5}
        assert link.queried == ["MEAS:VOLT:DC?", "MEAS:CURR:DC?"]

    def test_reader_commands(self, fresh):
        link = FakeTextLink({"*IDN?": "Keysight,34465A,MY123,1.0", "SYST:ERR?": '+0,"No error"'})
        dmm = ScpiReader(fresh("dmm"), link, {})
        assert set(type(dmm).commands) == {"identify", "query"}
        assert dmm.identify().startswith("Keysight") and dmm.state.identity is not None
        assert dmm.query("SYST:ERR?") == '+0,"No error"'

    def test_actuator_formats_the_demand_and_reads_back(self, fresh):
        link = FakeTextLink(lambda q: "11.98" if q == "MEAS:VOLT?" else "")
        psu = ScpiActuator(
            fresh("psu"), link, "SOUR:VOLT {value:.3f}", demand_unit="V", readback="MEAS:VOLT?"
        )
        assert psu.set_demand(12.0) == 11.98
        assert link.written == ["SOUR:VOLT 12.000"]
        assert psu.state.readback == 11.98 and psu.state.demand == 12.0
        assert psu.demand_unit is not None and psu.demand_unit.symbol == "V"

    def test_a_dead_instrument_raises_so_the_reader_goes_offline(self, fresh):
        dmm = ScpiReader(fresh("dmm"), FakeTextLink({}), {"v": ScpiMeasurand(query="X?", unit="V")})
        with pytest.raises(OSError):
            dmm.read(0)


class TestModbus:
    @pytest.mark.parametrize(
        ("kind", "words", "value"),
        [
            ("u16", [1234], 1234.0),
            ("s16", [0xFFFE], -2.0),
            ("u32", [0x0001, 0x0000], 65536.0),
            ("s32", [0xFFFF, 0xFFFF], -1.0),
            ("f32", [0x4048, 0xF5C3], pytest.approx(3.14, abs=1e-5)),
        ],
    )
    def test_register_kinds_decode_and_encode(self, kind, words, value):
        register = Register(address=0, kind=kind)
        assert register.decode(words) == value
        if kind != "f32":
            assert register.encode(value) == words

    def test_scale_offset_and_word_order(self):
        tenths = Register(address=100, scale=0.1, unit="°C")
        assert tenths.decode([215]) == pytest.approx(21.5)
        assert tenths.encode(21.5) == [215]
        little = Register(address=0, kind="u32", word_order="little")
        assert little.decode([0x0000, 0x0001]) == 65536.0

    def test_reader_and_actuator_over_a_fake_link(self, fresh):
        link = FakeRegisterLink({100: 215, 102: 0x4048, 103: 0xF5C3})
        bath = ModbusReader(
            fresh("bath"),
            link,
            {
                "temperature": Register(address=100, scale=0.1, unit="°C", precision=1),
                "flow": Register(address=102, kind="f32", unit="L/min"),
            },
        )
        (sample,) = bath.read(1)
        by_name = {m.name: v for m, v in sample.values.items()}
        assert by_name["temperature"] == pytest.approx(21.5)
        assert by_name["flow"] == pytest.approx(3.14, abs=1e-5)
        assert bath.measurands["flow"].unit.symbol == "L/min"

        setpoint = ModbusActuator(
            fresh("bath_sp"), link, Register(address=200, scale=0.1, unit="°C")
        )
        assert setpoint.set_demand(25.04) == pytest.approx(25.0), "quantised to the register"
        assert link.registers[200] == 250 and setpoint.state.written == [250]


TOML = """
name = "bench"

[links.bench]
tag = "fake_text"
replies = { "MEAS:VOLT:DC?" = "12.5", "MEAS:VOLT?" = "12.5" }

[links.chiller]
tag = "fake_registers"
registers = { 100 = 215 }

[[readers]]
period_s = 0.5
[readers.device]
tag = "scpi_reader"
name = "dmm"
link = "bench"
[readers.device.measurands.voltage]
query = "MEAS:VOLT:DC?"
unit = "V"

[[readers]]
[readers.device]
tag = "modbus_reader"
name = "bath"
link = "chiller"
registers = { temperature = { address = 100, scale = 0.1, unit = "°C" } }

[[actuators]]
tag = "scpi_actuator"
name = "psu"
link = "bench"
command = "SOUR:VOLT {value}"
demand_unit = "V"
readback = "MEAS:VOLT?"

[[loops]]
channel = "dmm.voltage"
actuator = "psu"
law = { tag = "PI", kp = 0.2, ki = 0.05 }
default = true
"""


class TestRigFile:
    def test_a_rig_builds_from_toml_with_fake_links(self, tmp_path, fresh):
        text = TOML.replace('"dmm"', f'"{fresh("dmm")}"').replace('"bath"', f'"{fresh("bath")}"')
        text = text.replace('"psu"', f'"{fresh("psu")}"').replace(
            "dmm.voltage", f"{text.split('name = "')[2].split('"')[0]}.voltage"
        )
        path = tmp_path / "rig.toml"
        path.write_text(text)
        rig = load_rig(path)
        try:
            names = list(rig.readers.by_name)
            assert len(names) == 2 and len(rig.actuators) == 1
            (psu_name,) = rig.actuators
            loop = rig.loops[psu_name]
            assert loop.settings.law is not None and loop.settings.law.tag == "PI"
            assert rig.loops.default == psu_name
            dmm_run = rig.readers.run(names[0])
            assert dmm_run.period_s == 0.5 and dmm_run.running
            assert rig.readers.run(names[1]).period_s is None, "no period: push only"
            assert rig.actuators[psu_name].set_demand(12.5) == 12.5
        finally:
            rig.readers.stop_all()

    def test_the_same_document_in_json_and_yaml(self, tmp_path):
        import yaml

        document = loads(TOML, ".toml")
        (tmp_path / "rig.json").write_text(json.dumps(document))
        (tmp_path / "rig.yaml").write_text(yaml.safe_dump(document))
        assert load_document(tmp_path / "rig.json") == document
        assert load_document(tmp_path / "rig.yaml") == document
        assert RigConfig.model_validate(document).name == "bench"

    def test_unknown_link_name_and_unknown_tag_are_refused(self):
        document = loads(TOML, ".toml")
        document["actuators"][0]["link"] = "nowhere"
        with pytest.raises(ValidationError, match="not declared"):
            RigConfig.model_validate(document)
        document = loads(TOML, ".toml")
        document["links"]["bench"]["tag"] = "telepathy"
        with pytest.raises(ValidationError):
            RigConfig.model_validate(document)

    def test_schema_is_a_discriminated_tree(self):
        schema = rig_schema()
        links = schema["properties"]["links"]["additionalProperties"]
        assert links["discriminator"]["propertyName"] == "tag"
        assert (
            "ScpiReaderConfigTagged" in schema["$defs"]
            and "ModbusTcpConfigTagged" in schema["$defs"]
        )

    def test_unknown_format_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="use one of"):
            load_document(tmp_path / "rig.ini")


class TestRigFileChecks:
    @pytest.mark.parametrize(
        ("mutate", "message"),
        [
            (lambda d: d["actuators"].append(dict(d["actuators"][0])), "must be unique"),
            (
                lambda d: d["loops"].__setitem__(0, {**d["loops"][0], "actuator": "ghost"}),
                "not declared",
            ),
            (
                lambda d: d["loops"].append(dict(d["loops"][0], default=False)),
                "driven by two loops",
            ),
            (
                lambda d: d["loops"].__setitem__(0, {**d["loops"][0], "channel": "dmmvoltage"}),
                "source.measurand",
            ),
            (
                lambda d: (
                    d["loops"].append({**d["loops"][0], "actuator": "psu2"})
                    or d["actuators"].append({**d["actuators"][0], "name": "psu2"})
                ),
                "only one loop can be the default",
            ),
        ],
    )
    def test_inconsistent_files_are_refused_with_their_own_names(self, mutate, message):
        document = loads(TOML, ".toml")
        mutate(document)
        with pytest.raises(ValidationError, match=message):
            RigConfig.model_validate(document)
