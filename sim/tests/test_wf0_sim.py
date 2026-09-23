"""Regression tests for the sim fault-injection review (N11, 23 Sep 2026).

N11: `SimDrive.disturb` was overwritten by the next `commit`, so it could
not exercise disturbance rejection on a regulated loop; a non-finite
offset also poisoned the plant while `health.ok` stayed true.
"""

from __future__ import annotations

import math

import pytest
from flyball.foundation.errors import NotFoundError
from flyball.rig import Rig

from flyball_sim import PlantConfig, SimDriveConfig, SteppedClock


def _bare_drive():
    plant = PlantConfig(model="lag", gain=10.0)
    drive = SimDriveConfig(link=plant, ports={"drive": "input"}).build("heater")
    return drive, drive.signals["drive"]


class TestDisturbPersists:
    def test_a_later_commit_does_not_wipe_out_the_disturbance(self):
        drive, signal = _bare_drive()
        drive.apply(signal, 0, 0.25)
        drive.commit(0)
        assert drive.plant.input == pytest.approx(0.25)

        assert drive.disturb("drive", 0.1) == {"drive": pytest.approx(0.35)}

        # A regulated loop keeps writing its own demand: this must not
        # erase the disturbance kick still in force.
        drive.apply(signal, 1_000_000_000, 0.4)
        drive.commit(1_000_000_000)
        assert drive.plant.input == pytest.approx(0.5), "0.4 demand + the surviving 0.1 kick"

    def test_offset_0_clears_it(self):
        drive, signal = _bare_drive()
        drive.apply(signal, 0, 0.25)
        drive.commit(0)
        drive.disturb("drive", 0.1)
        assert drive.disturb("drive", 0.0) == {"drive": pytest.approx(0.25)}

        drive.apply(signal, 1_000_000_000, 0.4)
        drive.commit(1_000_000_000)
        assert drive.plant.input == pytest.approx(0.4), "the kick was cleared"

    def test_a_fresh_disturb_replaces_the_earlier_one_rather_than_compounding(self):
        drive, signal = _bare_drive()
        drive.apply(signal, 0, 0.25)
        drive.commit(0)
        drive.disturb("drive", 0.1)
        assert drive.disturb("drive", 0.2) == {"drive": pytest.approx(0.45)}, "re-set, not added"

        drive.apply(signal, 1_000_000_000, 0.4)
        drive.commit(1_000_000_000)
        assert drive.plant.input == pytest.approx(0.6)

    def test_duration_s_lets_a_kick_expire_on_its_own(self):
        # `disturb`'s expiry is measured against the rig's own clock (`self.router.now_ns()`,
        # bound to it on `add_device`), so this needs a rig -- not a bare, unbound device.
        plant = PlantConfig(model="lag", gain=10.0)
        drive = SimDriveConfig(link=plant, ports={"drive": "input"}).build("heater")
        signal = drive.signals["drive"]
        rig = Rig()
        clock = rig.clock = SteppedClock(0)
        rig.add_device(drive)

        drive.apply(signal, clock.now_ns(), 0.25)
        drive.commit(clock.now_ns())
        rig.run_command(drive, "disturb", {"signal": "drive", "offset": 0.1, "duration_s": 1.0})

        clock.advance(0.5)  # still within the 1 s window
        drive.apply(signal, clock.now_ns(), 0.25)
        drive.commit(clock.now_ns())
        assert drive.plant.input == pytest.approx(0.35)

        clock.advance(1.5)  # past it
        drive.apply(signal, clock.now_ns(), 0.25)
        drive.commit(clock.now_ns())
        assert drive.plant.input == pytest.approx(0.25), "the kick expired"


class TestDisturbRefusesNonFinite:
    @pytest.mark.parametrize("offset", [math.nan, math.inf, -math.inf])
    def test_nan_and_inf_are_refused(self, offset):
        drive, _signal = _bare_drive()
        with pytest.raises(ValueError, match="must be finite"):
            drive.disturb("drive", offset)
        assert drive.plant.input == 0.0, "refused before it touched the plant"

    def test_an_unknown_signal_still_raises_not_found_before_the_finite_check(self):
        drive, _signal = _bare_drive()
        with pytest.raises(NotFoundError, match="no signal 'x'"):
            drive.disturb("x", math.nan)
