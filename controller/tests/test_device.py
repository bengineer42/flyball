"""The device tiers, their inference and checks, and command collection."""

from dataclasses import dataclass
from threading import RLock

import pytest
from pydantic import TypeAdapter

from flyball.core.device import Device, DeviceSettings, DeviceState, DriverConfig, command
from helpers import DutyHeater, DutyState


def test_tiers_are_inferred_from_property_annotations():
    assert DutyHeater.state_type is DutyState
    assert DutyHeater.config_type is DriverConfig
    assert DutyHeater.settings_type is DeviceSettings


def test_bare_device_answers_view_with_empty_tiers():
    class Bare(Device):
        pass

    view = Bare("b").view
    assert view.config == DriverConfig() and view.settings == DeviceSettings()
    assert view.state == DeviceState()
    assert Bare.blocking is False


def test_wrong_base_is_refused_at_definition():
    with pytest.raises(TypeError, match="must return a DeviceState"):

        class Wrong(Device):
            @property
            def state(self) -> int:  # type: ignore[override]
                return 1


def test_unschemable_state_is_refused_at_definition():
    @dataclass(frozen=True, kw_only=True)
    class Bad(DeviceState):
        lock: RLock

    with pytest.raises(TypeError, match="has no JSON schema"):

        class BadDevice(Device):
            @property
            def state(self) -> Bad:
                return Bad(lock=RLock())


def test_commands_are_collected_with_their_tags_and_docs():
    assert list(DutyHeater.commands) == ["set_duty", "off"]
    assert DutyHeater.commands["off"].method.__name__ == "switch_off"
    assert DutyHeater.commands["set_duty"].doc == "Drive the element at a fixed duty."


def test_subclass_extends_the_parent_s_commands_without_leaking_back():
    class Child(DutyHeater):
        @command
        def boost(self) -> None:
            """Stub."""

    assert list(Child.commands) == ["set_duty", "off", "boost"]
    assert "boost" not in DutyHeater.commands


def test_reserved_and_duplicate_tags_are_refused():
    with pytest.raises(ValueError, match="reserved"):

        class A(Device):
            @command(tag="schema")
            def s(self) -> None:
                """Stub."""

    with pytest.raises(ValueError, match="already used"):

        class B(Device):
            @command(tag="go")
            def one(self) -> None:
                """Stub."""

            @command(tag="go")
            def two(self) -> None:
                """Stub."""


def test_command_signature_must_be_schemable():
    class Opaque: ...

    with pytest.raises(TypeError, match=r"go\(x\) \(Opaque\)"):

        class Bad(Device):
            @command
            def go(self, x: Opaque) -> None:
                """Stub."""

    with pytest.raises(TypeError, match=r"go\(\) ->"):

        class BadReturn(Device):
            @command
            def go(self) -> RLock:
                """Stub."""


def test_state_schema_carries_conditions():
    props = TypeAdapter(DutyState).json_schema()["properties"]
    assert set(props) == {"conditions", "duty"}


def test_a_command_without_a_docstring_is_refused():
    with pytest.raises(TypeError, match="needs a docstring"):

        class Silent(Device):
            @command
            def go(self) -> None: ...
