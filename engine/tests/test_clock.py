"""Time/Duration: immutability, and hash consistent with eq."""

from __future__ import annotations

import pytest

from flyball.foundation.time.clock import Clock, Duration, Time


class TestImmutability:
    def test_no_public_mutators_remain(self):
        for name in ("set_nanoseconds", "set_seconds", "set_parts"):
            assert not hasattr(Time, name)
            assert not hasattr(Duration, name)

    def test_assigning_an_attribute_is_refused(self):
        t = Time.from_seconds(1.5)
        with pytest.raises(AttributeError):
            t._nanoseconds = 0  # type: ignore[misc]
        with pytest.raises(AttributeError):
            t.new_attr = 1  # type: ignore[attr-defined]

    def test_deleting_an_attribute_is_refused(self):
        t = Time.from_seconds(1.5)
        with pytest.raises(AttributeError):
            del t._nanoseconds


class TestHashConsistentWithEq:
    def test_equal_times_hash_equal(self):
        a = Time.from_seconds(1.5)
        b = Time.from_nanoseconds(1_500_000_000)
        assert a == b
        assert hash(a) == hash(b)

    def test_equal_durations_hash_equal(self):
        a = Duration.from_seconds(2.0)
        b = Duration.from_nanoseconds(2_000_000_000)
        assert a == b
        assert hash(a) == hash(b)

    def test_usable_as_a_dict_key_across_the_object_s_lifetime(self):
        t = Time.from_seconds(3.0)
        store = {t: "reading"}
        assert store[Time.from_nanoseconds(3_000_000_000)] == "reading"


class TestClockFromTime:
    def test_from_time_is_gone(self):
        assert not hasattr(Clock, "from_time")

    def test_from_nanoseconds_still_works(self):
        clock = Clock.from_nanoseconds(5_000_000_000)
        assert clock.start_time_ns == 5_000_000_000
