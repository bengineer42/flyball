"""The device tiers, their inference and checks, and command collection."""

from threading import RLock

import pytest

from flyball.foundation.device import Device, DriverConfig, command
from helpers import DutyHeater


def test_a_bare_device_has_no_read_or_commit():
    class Bare(Device):
        pass

    bare = Bare("b")
    assert Bare.readable is False and Bare.writable is False and Bare.blocking is False
    assert bare.config == DriverConfig()


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


def test_commands_are_collected_with_their_tags_and_docs():
    assert list(DutyHeater.commands) == ["set_duty", "off", "set_power"]
    assert DutyHeater.commands["off"].method.__name__ == "switch_off"
    assert DutyHeater.commands["set_duty"].doc == "Drive the element at a fixed duty."
    assert DutyHeater.commands["set_power"].demand_of == "power", (
        "a demand with no command linking to it gets a synthesised setter"
    )


def test_subclass_extends_the_parent_s_commands_without_leaking_back():
    class Child(DutyHeater):
        @command
        def boost(self) -> None:
            """Stub."""

    assert list(Child.commands) == ["set_duty", "off", "boost", "set_power"]
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


def test_a_command_without_a_docstring_is_refused():
    with pytest.raises(TypeError, match="needs a docstring"):

        class Silent(Device):
            @command
            def go(self) -> None: ...


def test_every_device_has_conditions_first_and_synthesised_setters_last():
    """`conditions` comes from the base class; `last.<tag>` from its own real commands."""
    assert [spec.name for spec in DutyHeater.TREE] == ["conditions", "power", "duty", "last"]
    heater = DutyHeater("h")
    assert list(heater.signals) == ["conditions", "power", "duty", "last.set_duty", "last.off"]
