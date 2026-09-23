#!/usr/bin/env python3
"""Search the driver catalogue -- `drivers-manifest.yaml` joined with `drivers-signals.yaml`.

    uv run python scripts/search_drivers.py --domain mushroom --tier config_only
    uv run python scripts/search_drivers.py --unit ppm
    uv run python scripts/search_drivers.py --dimension Fraction --json

Every filter is an exact match on one field except `--type`/`--text`, which are substring
matches. Filters combine with AND. `--unit`/`--dimension` match if any of a driver's signals
(from `drivers-signals.yaml`, itself introspected from the code by `index_signals.py` -- see
that file) has the given unit symbol or dimension label.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "drivers-manifest.yaml"
SIGNALS = ROOT / "drivers-signals.yaml"


def load_catalogue(
    manifest_path: Path = MANIFEST, signals_path: Path = SIGNALS
) -> list[dict[str, Any]]:
    """Every entry from the manifest, each with its introspected `signals:` list joined in."""
    manifest = yaml.safe_load(manifest_path.read_text())
    signals_by_type: dict[str, list[dict[str, str]]] = {}
    if signals_path.exists():
        # The generated file has a couple of trailing `#` comment lines after the YAML
        # document; safe_load stops at the document end, so this is just `{by_type: {...}}`.
        signals_by_type = (yaml.safe_load(signals_path.read_text()) or {}).get("by_type", {}) or {}
    entries = list(manifest.get("sensors", [])) + list(manifest.get("links", []))
    for entry in entries:
        entry["signals"] = signals_by_type.get(entry.get("type", ""), [])
    return entries


def matches(entry: dict[str, Any], args: argparse.Namespace) -> bool:
    def field(name: str) -> str:
        return str(entry.get(name, "")).lower()

    if args.type and args.type.lower() not in field("type"):
        return False
    if args.category and args.category.lower() != field("category"):
        return False
    if args.interface and args.interface.lower() != field("interface"):
        return False
    if args.tier and args.tier.lower() != field("tier"):
        return False
    if args.status and args.status.lower() != field("status"):
        return False
    if args.manufacturer and args.manufacturer.lower() not in field("manufacturer"):
        return False
    if args.domain and args.domain.lower() not in [d.lower() for d in entry.get("domains", [])]:
        return False
    if args.text:
        haystack = json.dumps(entry).lower()
        if args.text.lower() not in haystack:
            return False
    signals = entry.get("signals", [])
    if args.unit and not any(s.get("unit") == args.unit for s in signals):
        return False
    if args.dimension:
        wanted = args.dimension.lower()
        return any(s.get("dimension", "").lower() == wanted for s in signals)
    return True


def render_table(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "(no matches)"
    rows = [
        (
            e.get("type", ""),
            e.get("part_number", ""),
            e.get("manufacturer", ""),
            e.get("category", ""),
            e.get("tier", ""),
            e.get("status", ""),
            ", ".join(f"{s['unit']}" for s in e.get("signals", [])) or "-",
        )
        for e in entries
    ]
    header = ("type", "part_number", "manufacturer", "category", "tier", "status", "units")
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    lines = [" | ".join(h.ljust(w) for h, w in zip(header, widths, strict=True))]
    lines.append("-+-".join("-" * w for w in widths))
    for r in rows:
        lines.append(" | ".join(str(c).ljust(w) for c, w in zip(r, widths, strict=True)))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--type", help="substring match on the driver type")
    parser.add_argument("--category")
    parser.add_argument("--interface")
    parser.add_argument("--tier", choices=["config_only", "generic_link", "bespoke_driver"])
    parser.add_argument("--status", choices=["done", "in_progress", "planned"])
    parser.add_argument("--manufacturer", help="substring match")
    parser.add_argument("--domain", help="which roadmap lead this serves, e.g. mushroom")
    parser.add_argument("--unit", help="a signal's unit symbol, e.g. ppm, °C, %%RH")
    parser.add_argument("--dimension", help="a signal's physical dimension, e.g. Temperature")
    parser.add_argument("--text", help="substring match over the whole entry")
    parser.add_argument("--json", action="store_true", help="print matches as JSON, not a table")
    args = parser.parse_args(argv)

    catalogue = load_catalogue()
    found = [e for e in catalogue if matches(e, args)]

    if args.json:
        json.dump(found, sys.stdout, indent=2)
        print()
    else:
        print(render_table(found))
        print(f"\n{len(found)} of {len(catalogue)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
