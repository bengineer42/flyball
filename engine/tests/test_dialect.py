"""The program file dialect: normalising steps, and the schema it emits."""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass
from typing import Any, cast

import pytest
import yaml

from flyball.foundation.time import Duration, Rate, TimeUnit
from flyball.foundation.typing import Percent
from flyball.interfaces.server.dialect import (
    Dialect,
    Modifier,
    StepError,
    commands_from_yaml,
    foldable,
    normalise_step,
    program_schema,
)
from flyball.sequencing.step import Step


@pytest.fixture
def commands(fresh):
    """Six commands under unique tags, so tests never see each other's."""
    tags = {k: fresh(k) for k in ("setpoint", "ramp", "flag", "twice", "timer", "ask")}

    @dataclass(frozen=True)
    class Setpoint(Step, type=tags["setpoint"], primary="at"):
        """Go to this value and hold."""

        at: Percent

        def run(self, rig: Any, operator: Any = None) -> Any: ...

    @dataclass(frozen=True)
    class Ramp(Step, type=tags["ramp"]):
        to: Percent
        pace: Rate | Duration
        start: Percent | str = "setpoint"

        def run(self, rig: Any, operator: Any = None) -> Any: ...

    @dataclass(frozen=True)
    class Flag(Step, type=tags["flag"], primary="flag"):
        flag: str

        def run(self, rig: Any, operator: Any = None) -> Any: ...

    @dataclass(frozen=True)
    class Twice(Step, type=tags["twice"]):
        a: Duration
        b: Duration

        def run(self, rig: Any, operator: Any = None) -> Any: ...

    @dataclass(frozen=True)
    class Timer(Step, type=tags["timer"], primary="duration"):
        duration: Duration
        message: str | None = None
        timeout: Duration | None = None

        def run(self, rig: Any, operator: Any = None) -> Any: ...

    @dataclass(frozen=True)
    class Ask(Step, type=tags["ask"], primary="message"):
        message: str
        timeout: Duration | None = None

        def run(self, rig: Any, operator: Any = None) -> Any: ...

    return tags, {
        "Setpoint": Setpoint,
        "Ramp": Ramp,
        "Flag": Flag,
        "Twice": Twice,
        "Timer": Timer,
        "Ask": Ask,
    }


@pytest.fixture
def dialect(commands):
    tags, classes = commands
    settle = {
        "type": "object",
        "properties": {"within": {"type": "number", "exclusiveMinimum": 0}},
        "additionalProperties": False,
    }
    return Dialect(
        modifiers=(
            Modifier("settle", "until", settle),
            Modifier("minutes", "for", {"type": "number"}),
        ),
        steps={t: classes[k.capitalize()] for k, t in tags.items()},
    )


class TestNormalise:
    def test_scalar_shorthand_means_the_primary_field(self, dialect, commands):
        tags, _ = commands
        assert normalise_step({tags["setpoint"]: 50}, dialect) == {
            "command": {"type": tags["setpoint"], "at": 50}
        }
        assert normalise_step({tags["flag"]: "loaded"}, dialect)["command"]["flag"] == "loaded"

    def test_modifiers_land_in_their_fields(self, dialect, commands):
        tags, _ = commands
        step = normalise_step(
            {tags["setpoint"]: 80, "settle": {"within": 0.5}, "minutes": 5}, dialect
        )
        assert step["until"] == {"within": 0.5} and step["for"] == 5

    def test_flat_time_keys_fold_into_the_one_time_field(self, dialect, commands):
        tags, classes = commands
        fold = foldable(classes["Ramp"])
        assert fold is not None and fold[0] == "pace"
        assert foldable(classes["Twice"]) is None, "two time fields: no fold"
        step = normalise_step({tags["ramp"]: {"to": 60, "per_minute": 2}}, dialect)
        assert step["command"]["pace"] == {"per_minute": 2}
        step = normalise_step({tags["ramp"]: {"to": 60, "minutes": 1, "seconds": 30}}, dialect)
        assert step["command"]["pace"] == {"minutes": 1, "seconds": 30}

    def test_timeout_is_never_folded(self, dialect, commands):
        """Flat keys fold into the duration beside a timeout; the timeout stays nested."""
        tags, classes = commands
        fold = foldable(classes["Timer"])
        assert fold is not None and fold[0] == "duration"
        step = normalise_step(
            {tags["timer"]: {"minutes": 5, "message": "soak", "timeout": {"minutes": 10}}},
            dialect,
        )
        assert step["command"]["duration"] == {"minutes": 5}
        assert step["command"]["timeout"] == {"minutes": 10}
        program = commands_from_yaml(
            f"steps: [{{{tags['timer']}: {{minutes: 5, timeout: 20}}}}]", dialect
        )
        timer = program[0]
        assert timer.duration == Duration(300) and timer.timeout == Duration(20)

    def test_a_timeout_only_command_refuses_flat_keys(self, dialect, commands):
        tags, classes = commands
        assert foldable(classes["Ask"]) is None
        inside = r"write the timeout inside it: `timeout: \{minutes: 10\}`"
        with pytest.raises(StepError, match=inside):
            normalise_step({tags["ask"]: {"message": "load", "minutes": 10}}, dialect, 2)
        nested = {"message": "load", "timeout": {"minutes": 10}}
        step = normalise_step({tags["ask"]: nested}, dialect)
        assert step["command"]["timeout"] == {"minutes": 10}

    @pytest.mark.parametrize(
        ("bad", "message"),
        [
            ({"setpint": 50}, "unknown key"),
            ({"RAMP": 60}, "takes a mapping"),
            ({"SETPOINT": {"type": "x"}}, "'type' is not an argument"),
            ("setpoint", "expected a mapping"),
            ({"RAMP": {"to": 60, "pace": 600, "minutes": 1}}, "not both"),
            ({"TWICE": {"a": 1, "minutes": 2}}, "ambiguous"),
        ],
    )
    def test_refusals(self, dialect, commands, bad, message):
        tags, _ = commands
        if isinstance(bad, dict):
            bad = {tags.get(k.lower(), k): v for k, v in bad.items()}
        with pytest.raises(StepError, match=message):
            normalise_step(bad, dialect, 3)

    def test_two_commands_in_one_step_is_refused(self, dialect, commands):
        tags, _ = commands
        with pytest.raises(StepError, match="exactly one command"):
            normalise_step({tags["setpoint"]: 1, tags["ramp"]: {"to": 1, "pace": 1}}, dialect)


def test_commands_from_yaml_builds_a_program(dialect, commands):
    tags, classes = commands
    text = textwrap.dedent(f"""
        name: demo
        steps:
          - {tags["setpoint"]}: 50
          - {tags["ramp"]}: {{to: 60, pace: {{seconds: 600}}}}
          - {tags["ramp"]}: {{to: 60, per_minute: 2, start: reading}}
          - {tags["flag"]}: "something"
    """)
    program = commands_from_yaml(text, dialect)
    assert program.name == "demo" and len(program) == 4
    assert program[0] == classes["Setpoint"](at=50.0)
    ramp_a, ramp_b = cast(Any, program[1]), cast(Any, program[2])
    assert ramp_a.pace == Duration(600)
    assert ramp_b.pace == Rate(2.0, TimeUnit.MINUTE) and ramp_b.start == "reading"


def test_program_schema_is_externally_tagged_with_shorthand_and_folds(dialect, commands):
    tags, _ = commands
    schema = program_schema(dialect)
    branches = {b["required"][0]: b for b in schema["properties"]["steps"]["items"]["oneOf"]}
    assert set(branches) == set(tags.values())
    setpoint = branches[tags["setpoint"]]["properties"][tags["setpoint"]]
    assert [a.get("type") for a in setpoint["anyOf"]] == ["number", "object"], "scalar shorthand"
    ramp = branches[tags["ramp"]]["properties"][tags["ramp"]]
    assert {"pace", "per_minute", "seconds"} <= set(ramp["properties"]) and ramp["required"] == [
        "to"
    ]
    assert "settle" in branches[tags["flag"]]["properties"], "modifiers on every branch"
    assert branches[tags["setpoint"]]["description"] == "Go to this value and hold."


def test_a_program_loads_from_toml_json_and_yaml(tmp_path, dialect, commands):
    tags, classes = commands
    from flyball.interfaces.server.dialect import program_from_file

    steps = [
        {tags["setpoint"]: 50},
        {tags["ramp"]: {"to": 60, "per_minute": 2}},
        {tags["flag"]: "done"},
    ]
    (tmp_path / "p.json").write_text(json.dumps({"name": "j", "steps": steps}))
    (tmp_path / "p.yaml").write_text(yaml.safe_dump({"name": "y", "steps": steps}))
    (tmp_path / "p.toml").write_text(
        f'name = "t"\n'
        f"[[steps]]\n{tags['setpoint']} = 50\n"
        f"[[steps]]\n{tags['ramp']} = {{ to = 60, per_minute = 2 }}\n"
        f'[[steps]]\n{tags["flag"]} = "done"\n'
    )
    for suffix in ("json", "yaml", "toml"):
        program = program_from_file(tmp_path / f"p.{suffix}", dialect)
        assert len(program) == 3 and program[0] == classes["Setpoint"](at=50.0)
        assert cast(Any, program[1]).pace == Rate(2.0, TimeUnit.MINUTE)
