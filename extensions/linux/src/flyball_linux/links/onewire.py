"""1-Wire over the kernel's `w1` bus: `/sys/bus/w1/devices/<id>/w1_slave`."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from flyball.foundation.config import Config
from pydantic import Field


@runtime_checkable
class OneWireLink(Protocol):
    """Devices by id, each read as the text the kernel driver reports."""

    def devices(self) -> list[str]: ...

    def read(self, device: str) -> str: ...


class FakeOneWire:
    """Device id -> the `w1_slave` text (or a list of them, served in turn)."""

    def __init__(self, texts: dict[str, str | list[str]] | None = None) -> None:
        self.texts = {
            k: (list(v) if isinstance(v, list) else [v]) for k, v in (texts or {}).items()
        }
        self.reads: list[str] = []

    def devices(self) -> list[str]:
        return sorted(self.texts)

    def read(self, device: str) -> str:
        self.reads.append(device)
        try:
            queue = self.texts[device]
        except KeyError:
            raise OSError(f"no 1-Wire device {device}") from None
        return queue[0] if len(queue) == 1 else queue.pop(0)


class FakeOneWireConfig(Config[OneWireLink], tag="fake_onewire"):
    texts: dict[str, str | list[str]] = Field(default_factory=dict)

    def build(self) -> OneWireLink:
        return FakeOneWire(self.texts)


class SysfsOneWire:
    """The kernel's `w1` bus. Needs the `w1-gpio` overlay (or a real 1-Wire master) enabled."""

    def __init__(self, root: str | Path = "/sys/bus/w1/devices") -> None:
        self.root = Path(root)
        if not self.root.is_dir():
            raise OSError(f"{self.root} does not exist; is the 1-Wire overlay enabled?")

    def devices(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if not p.name.startswith("w1_bus"))

    def read(self, device: str) -> str:
        return (self.root / device / "w1_slave").read_text()


class OneWireConfig(Config[OneWireLink], tag="onewire"):
    root: str = "/sys/bus/w1/devices"

    def build(self) -> OneWireLink:
        return SysfsOneWire(self.root)


ONEWIRE_LINKS = (FakeOneWireConfig, OneWireConfig)
OneWireLinkConfig = Config.union(*ONEWIRE_LINKS)

__all__ = [
    "ONEWIRE_LINKS",
    "FakeOneWire",
    "FakeOneWireConfig",
    "OneWireConfig",
    "OneWireLink",
    "OneWireLinkConfig",
    "SysfsOneWire",
]
