"""`SysfsPwm` against a fake `/sys/class/pwm` tree: the export race, not a real kernel."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from flyball_linux.links.pwm import SysfsPwm


class TestSysfsPwmSeedsPeriodFromTheKernel:
    def test_configure_reads_the_existing_period_before_its_first_write(self, tmp_path, monkeypatch):
        chip = tmp_path / "pwmchip0"
        pwm0 = chip / "pwm0"
        pwm0.mkdir(parents=True)
        (pwm0 / "period").write_text("5000000")
        (pwm0 / "duty_cycle").write_text("0")
        (pwm0 / "enable").write_text("0")

        writes: list[str] = []
        original_write_text = Path.write_text

        def recording_write_text(self: Path, data: str, *a: object, **kw: object) -> int | None:
            writes.append(self.name)
            return original_write_text(self, data, *a, **kw)

        monkeypatch.setattr(Path, "write_text", recording_write_text)
        link = SysfsPwm(root=tmp_path)
        # Shrinking from the kernel's already-set 5 ms period: duty must be
        # written before period, or the kernel would refuse the too-long duty.
        link.configure(0, 1_000_000, 500_000)

        assert writes == ["duty_cycle", "period"], (
            "the period read from the kernel, not the driver's own empty cache, decided the order"
        )


class TestSysfsPwmWaitsForEnable:
    def test_configure_waits_for_enable_to_become_writable_after_export(self, tmp_path, monkeypatch):
        chip = tmp_path / "pwmchip0"
        chip.mkdir(parents=True)
        pwm0 = chip / "pwm0"
        original_write_text = Path.write_text

        def fake_write_text(self: Path, data: str, *a: object, **kw: object) -> int | None:
            if self.name == "export":
                pwm0.mkdir(exist_ok=True)
                (pwm0 / "period").write_text("0")
                (pwm0 / "duty_cycle").write_text("0")
                (pwm0 / "enable").write_text("0")
                os.chmod(pwm0 / "enable", 0o444)  # root-owned until udev grants the group perm

                def grant() -> None:
                    time.sleep(0.05)
                    os.chmod(pwm0 / "enable", 0o644)

                threading.Thread(target=grant, daemon=True).start()
                return None
            return original_write_text(self, data, *a, **kw)

        monkeypatch.setattr(Path, "write_text", fake_write_text)
        link = SysfsPwm(root=tmp_path)
        start = time.monotonic()
        link.configure(0, 1_000_000, 500_000)
        elapsed = time.monotonic() - start

        assert elapsed < 1.0, "returned once the grant landed, not after the full 1 s ceiling"
        assert os.access(pwm0 / "enable", os.W_OK)
