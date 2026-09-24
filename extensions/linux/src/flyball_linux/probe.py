"""What this machine actually has: `flyball-linux probe`.

Lists the I2C, SPI, GPIO, PWM and 1-Wire devices the kernel exposes, scans
each I2C bus for chips, and prints rig-file fragments for the ones it
recognises. Reads only: an I2C scan is a read of one byte per address, the
same as `i2cdetect -r`, which a few chips (some EEPROMs and DACs) dislike;
pass `--no-scan` on a bus that matters.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

KNOWN: dict[int, tuple[str, str]] = {
    0x40: ("ina219 / htu21d / si7021", "i2c_table"),
    0x44: ("sht4x / sht3x", "sht4x"),
    0x45: ("sht4x-B / sht3x-B", "sht4x"),
    0x48: ("ads1115 / tmp102 / lm75", "ads1115"),
    0x49: ("ads1115 (ADDR=VDD) / tmp102", "ads1115"),
    0x4A: ("ads1115 (ADDR=SDA)", "ads1115"),
    0x4B: ("ads1115 (ADDR=SCL)", "ads1115"),
    0x18: ("mcp9808", "i2c_table"),
    0x1D: ("adxl345 / lsm303", "i2c_table"),
    0x3C: ("ssd1306 display", ""),
    0x50: ("eeprom", ""),
    0x68: ("ds1307 / mpu6050 / pcf8523", "i2c_table"),
    0x76: ("bme280 / bmp280", "i2c_table"),
    0x77: ("bme280 / bmp280 / bmp180", "i2c_table"),
}
"""Address -> (what usually lives there, the type to start from)."""


def model(root: Path = Path("/proc/device-tree")) -> str | None:
    """The board's own name, where the device tree gives one."""
    try:
        return (root / "model").read_text().rstrip("\0\n")
    except OSError:
        return None


def _numeric(name: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", name))


def buses(dev: Path = Path("/dev"), sys_root: Path = Path("/sys")) -> dict[str, list[str]]:
    """Every bus device by kind, as the kernel names them."""
    found = {
        "i2c": sorted((p.name for p in dev.glob("i2c-*")), key=_numeric),
        "spi": sorted((p.name for p in dev.glob("spidev*")), key=_numeric),
        "gpio": sorted((p.name for p in dev.glob("gpiochip*")), key=_numeric),
        "pwm": sorted((p.name for p in (sys_root / "class/pwm").glob("pwmchip*")), key=_numeric),
        "onewire": sorted(
            p.name
            for p in (sys_root / "bus/w1/devices").glob("*")
            if not p.name.startswith("w1_bus")
        ),
    }
    return found


def gpio_lines(chip: str, dev: Path = Path("/dev")) -> tuple[str, int] | None:
    """(label, line count) for a chip, through gpiod if it is installed."""
    try:
        import gpiod
    except ImportError:
        return None
    try:
        with gpiod.Chip(str(dev / chip)) as c:
            info = c.get_info()
            return info.label, info.num_lines
    except OSError:
        return None


def scan_i2c(bus: int) -> Iterator[int]:
    """Addresses that answer a one-byte read on `/dev/i2c-<bus>`. Needs the `i2c` extra."""
    from smbus2 import SMBus

    with SMBus(bus) as smbus:
        for address in range(0x03, 0x78):
            try:
                smbus.read_byte(address)
            except OSError:
                continue
            yield address


def fragment(bus_name: str, address: int) -> str:
    """A `devices:` entry to start from, for a recognised address."""
    what, driver = KNOWN.get(address, ("unknown", ""))
    if not driver:
        return f"# 0x{address:02x}: {what}; no flyball driver for it"
    link = bus_name.replace("i2c-", "i2c")
    fields = [f"driver: {driver}", "poll_s: 1", f"link: {link}"]
    if driver != "sht4x":
        fields.append(f"i2c_address: 0x{address:02x}")
    if driver == "ads1115":
        fields.append("channels: { volts: { channel: 0 } }")
    note = ""
    if driver == "i2c_table":
        fields.append("registers: {}")
        note = "   # registers from the datasheet: address, length, signed, scale, unit"
    return f"# 0x{address:02x}: {what}\n{driver}_{address:02x}: {{ {', '.join(fields)} }}{note}"


def report(scan: bool = True) -> str:
    out: list[str] = []
    if (name := model()) is not None:
        out.append(f"board: {name}")
    found = buses()
    for kind, names in found.items():
        out.append(f"{kind}: {', '.join(names) if names else '-'}")
    for chip in found["gpio"]:
        if (info := gpio_lines(chip)) is not None:
            label, count = info
            out.append(f"  {chip}: {label}, {count} lines")
    if scan:
        for name in found["i2c"]:
            match = re.fullmatch(r"i2c-(\d+)", name)
            if match is None:
                continue
            try:
                addresses = list(scan_i2c(int(match.group(1))))
            except ImportError:
                out.append(f"  {name}: install flyball-linux[i2c] to scan")
                continue
            except OSError as e:
                out.append(f"  {name}: {e}")
                continue
            out.append(
                f"  {name}: {', '.join(f'0x{a:02x}' for a in addresses) or 'nothing answered'}"
            )
            out.extend(fragment(name, a) for a in addresses)
    for device in found["onewire"]:
        out.append(
            f"# 1-Wire {device}\nprobe_{device[-4:]}:"
            f' {{ driver: ds18b20, poll_s: 2, link: w1, probe_id: "{device}" }}'
        )
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="flyball-linux", description="What this machine actually has on its buses."
    )
    sub = parser.add_subparsers(dest="action")
    probe = sub.add_parser("probe", help="list this machine's buses and what is on them")
    probe.add_argument("--no-scan", action="store_true", help="do not read from I2C addresses")
    args = parser.parse_args(argv)
    if args.action != "probe":
        parser.print_help()
        return 0
    if not sys.platform.startswith("linux"):
        print("flyball-linux: not Linux; nothing to probe", file=sys.stderr)
        return 2
    print(report(scan=not args.no_scan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
