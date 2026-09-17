"""The knobs on a simulated rig, and writing what you set back to a file.

A rig built from a file whose links are all `sim_*` or `fake_*` is a
simulation; its plants' parameters and its clock's speed can be changed
while it runs, from the API (`/api/sim`) or the CLI (`flyball sim`). What is
set is kept, and `save` writes the rig file with the new values so the next
run starts from them.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from flyball.core.config import Config
from flyball.core.errors import ConflictError, NotFoundError
from flyball.core.files import SUFFIXES
from flyball.core.signal import Signal
from flyball.runtime.config import ClockEntry, RigConfig, is_simulated
from flyball.runtime.rig import Rig
from flyball.runtime.stats import noise, rate
from flyball.sim.clock import ScaledClock, SteppedClock
from flyball.sim.devices import SimDaq
from flyball.sim.furnace import MultiPlant
from flyball.sim.plant import Fopdt, Integrator, Lag, Noisy

__all__ = ["Simulation", "resolve_live"]


class Simulation:
    """A running simulated rig: its clock, its plants, and the file they came from.

    Args:
        rig: The rig, built from `config`.
        config: What built it; kept up to date with every change.
        document: The file as loaded (before its board was applied), so a
            save keeps the author's keys and comments' neighbours. Optional.
        path: Where the file came from; the default place to save.
    """

    def __init__(
        self,
        rig: Rig,
        config: RigConfig,
        document: dict[str, Any] | None = None,
        path: str | Path | None = None,
    ) -> None:
        if not is_simulated(config.links):
            raise ConflictError("not a simulation: a link is real hardware")
        self.rig = rig
        self.config = config
        self.document = dict(document) if document is not None else None
        self.path = Path(path) if path is not None else None
        self._changed: set[str] = set()
        self._last_tick: tuple[int, int] | None = None

    # region The clock

    @property
    def speed(self) -> float:
        return float(getattr(self.rig.clock, "speed", 1.0))

    @property
    def stepped(self) -> bool:
        return isinstance(self.rig.clock, SteppedClock)

    def set_speed(self, speed: float) -> float:
        """Run the rig's time at `speed` times wall time from now on.

        Raises:
            ConflictError: The rig's clock is not one whose speed can change.
            ValueError: `speed` is not positive.
        """
        clock = self.rig.clock
        if not isinstance(clock, ScaledClock):
            raise ConflictError(f"the rig's clock is a {type(clock).__name__}; it has no speed")
        clock.set_speed(speed)
        self.config = self.config.model_copy(update={"clock": ClockEntry(speed=speed)})
        self._changed.add("clock")
        return clock.speed

    def step(self, seconds: float) -> int:
        """Advance a stepped clock; return the new time.

        Raises:
            ConflictError: The clock runs on its own.
        """
        clock = self.rig.clock
        if not isinstance(clock, SteppedClock):
            raise ConflictError("the clock is not stepped; set `clock.stepped = true` to step it")
        return clock.advance(seconds)

    # endregion
    # region The plants

    @property
    def plants(self) -> dict[str, Any]:
        """Every simulated plant link (`sim_plant`, `sim_furnace`, ...), by name.

        A link counts when its config can `retune` what it built.
        """
        return {
            name: link
            for name, link in self.rig.links.items()
            if hasattr(self.config.links.get(name), "retune")
        }

    def plant_config(self, name: str) -> Any:
        config = self.config.links.get(name)
        if not hasattr(config, "retune"):
            raise NotFoundError(f"no simulated plant {name!r}; there are {sorted(self.plants)}")
        return config

    def plant_state(self, name: str) -> dict[str, Any]:
        """A single-port plant's `input` and `output`; a multi-port one's `inputs` and `outputs`."""
        plant = self.plants.get(name)
        if plant is None:
            raise NotFoundError(f"no simulated plant {name!r}; there are {sorted(self.plants)}")
        if isinstance(plant, MultiPlant):
            return {
                "inputs": dict(plant.inputs),
                "outputs": {port: plant.output(port) for port in plant.output_names},
            }
        return {"input": plant.input, "output": plant.output}

    def set_plant(self, name: str, **parameters: Any) -> Any:
        """Change a plant's parameters while it runs; the new config is what a save writes.

        Raises:
            NotFoundError: No such plant.
            ValueError: A parameter the plant does not have, an invalid value,
                or a change of `model`.
        """
        current = self.plant_config(name)
        if "model" in parameters and parameters["model"] != getattr(current, "model", None):
            raise ValueError("a plant's model cannot change while it runs; edit the file")
        # The tagged form, as the file's union holds, so the config still dumps as its tag.
        model = Config.registry[current.config_tag].tagged()
        updated = model.model_validate({**current.model_dump(), **parameters})
        updated.retune(self.plants[name])  # type: ignore[attr-defined]
        links = {**self.config.links, name: updated}
        self.config = self.config.model_copy(update={"links": links})
        self._changed.add(f"links.{name}")
        return updated

    def reset_plant(
        self, name: str, output: float | None = None, input: float | None = None
    ) -> dict[str, Any]:
        """Put a plant at a state: its output (the process variable) and/or its input.

        A multi-port plant is put at `output` everywhere (its own `reset`);
        `input` then applies to every input.
        """
        plant = self.plants.get(name)
        if plant is None:
            raise NotFoundError(f"no simulated plant {name!r}; there are {sorted(self.plants)}")
        if isinstance(plant, MultiPlant):
            if output is not None:
                plant.reset(output)  # type: ignore[attr-defined]
            if input is not None:
                for port in plant.inputs:
                    plant.inputs[port] = input
            return self.plant_state(name)
        inner: Any = plant.plant if isinstance(plant, Noisy) else plant
        if output is not None:
            match inner:
                case Lag() | Integrator():
                    inner.value = output
                case Fopdt():
                    inner._lag.value = output
                    inner._pipe.clear()
        if input is not None:
            plant.input = input
        return self.plant_state(name)

    # endregion
    # region What you set, as a file

    def readings(self, name: str) -> dict[str, dict[str, Any]]:
        """What the rig last delivered on each signal read off a plant, by the signal's address.

        The delivered value (noise and all, not the model's state), its unit
        and precision, which device and port it came off, and how old it is
        in rig seconds. Only signals some `sim_daq` reads appear.
        """
        out: dict[str, dict[str, Any]] = {}
        now_ns = self.rig.clock.now_ns()
        for device, signal, port in self._read_ports(name):
            reading = self.rig.latest.get(signal)
            if reading is None:
                continue
            out[signal.address] = {
                "value": reading.value,
                "unit": signal.unit.symbol,
                "precision": signal.spec.precision,
                "device": device.name,
                "port": port,
                "age_s": (now_ns - reading.time_ns) / 1e9,
            }
        return out

    def stats(self, name: str) -> dict[str, dict[str, float]]:
        """What the recent readings show, per signal read off the plant, by address.

        `noise`: σ about a short moving mean over the last readings, in the
        signal's unit; `rate_per_min`: the slope of the last minute of them.
        A signal is left out of a statistic there are too few readings for.
        """
        out: dict[str, dict[str, float]] = {"noise": {}, "rate_per_min": {}}
        now_ns = self.rig.clock.now_ns()
        for _, signal, _ in self._read_ports(name):
            recent = self.rig.recent_readings(signal)
            if (sigma := noise(recent)) is not None:
                out["noise"][signal.address] = sigma
            last_minute = [r for r in recent if now_ns - r.time_ns <= 60_000_000_000]
            if (slope := rate(last_minute)) is not None:
                out["rate_per_min"][signal.address] = slope
        return out

    def _read_ports(self, name: str) -> list[tuple[SimDaq, Signal, str]]:
        """Each (device, signal, port) some `sim_daq` reads off the plant `name`."""
        plant = self.plants.get(name)
        if plant is None:
            raise NotFoundError(f"no simulated plant {name!r}; there are {sorted(self.plants)}")
        return [
            (device, device.signals[signal_name], port)
            for device in self.rig.devices.values()
            if isinstance(device, SimDaq) and device.plant is plant
            for signal_name, port in device.ports.items()
        ]

    def links(self, name: str) -> dict[str, str]:
        """Each config field with a `live` link, to the path it points at (see `resolve_live`)."""
        config = self.plant_config(name)
        out = {}
        for field, info in type(config).model_fields.items():
            extra = info.json_schema_extra
            if isinstance(extra, dict) and isinstance(path := extra.get("live"), str):
                out[field] = path
        return out

    def live(self, name: str, root: dict[str, Any] | None = None) -> dict[str, Any]:
        """For each config field with a `live` link, the value(s) it stands beside.

        A field declares `json_schema_extra={"live": path}` and the path is
        resolved against the plant as `describe` reports it (`root`, built
        if not given). Fields with no link are parameters, not states; a
        path that finds nothing is left out.
        """
        if root is None:
            root = self.plant_description(name)
        resolved = {}
        for field, path in self.links(name).items():
            value = resolve_live(path, root)
            if value not in (None, {}):
                resolved[field] = value
        return resolved

    def measured_speed(self) -> float | None:
        """How fast the rig's time is actually passing against wall time, over the last call."""
        now_real, now_rig = time.monotonic_ns(), self.rig.clock.now_ns()
        last = self._last_tick
        self._last_tick = (now_real, now_rig)
        if last is None or now_real - last[0] < 100_000_000:  # under 0.1 s: too short to say
            return None
        return (now_rig - last[1]) / (now_real - last[0])

    def plant_description(self, name: str) -> dict[str, Any]:
        """A plant as the API reports it: config, state, what is read from it, and the pairing.

        `links` names each linked config field's path; `live` is what the
        path found, so a client need not resolve it.
        """
        root = {
            "config": self.plant_config(name).model_dump(mode="json"),
            **self.plant_state(name),
            "readings": self.readings(name),
            "stats": self.stats(name),
        }
        return {**root, "links": self.links(name), "live": self.live(name, root)}

    def describe(self) -> dict[str, Any]:
        """Everything adjustable, as the API reports it, each value beside what is being read."""
        return {
            "name": self.config.name,
            "path": str(self.path) if self.path is not None else None,
            "clock": {
                "speed": self.speed,
                "measured": self.measured_speed(),
                "stepped": self.stepped,
                "now_ns": self.rig.clock.now_ns(),
            },
            "plants": {name: self.plant_description(name) for name in self.plants},
            "changed": sorted(self._changed),
        }

    def config_document(self) -> dict[str, Any]:
        """The rig file as it now stands: the loaded document with every change applied.

        With no document to start from, the whole config is dumped.
        """
        if self.document is None:
            dumped = self.config.model_dump(mode="json", exclude_none=True)
            dumped["links"] = {name: _tagged(c) for name, c in self.config.links.items()}
            return dumped
        out = dict(self.document)
        if "clock" in self._changed:
            out["clock"] = {"speed": self.speed}
        links = dict(out.get("links", {}))
        for key in self._changed:
            if key.startswith("links."):
                name = key.removeprefix("links.")
                links[name] = _tagged(self.plant_config(name))
        if links:
            out["links"] = links
        return out

    def save(self, path: str | Path | None = None) -> Path:
        """Write the current config in the suffix's format; default: where it was loaded from.

        Raises:
            ValueError: No path and none to default to, or an unknown suffix.
        """
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ValueError("say where to save: the rig was not loaded from a file")
        if target.suffix.lower() not in SUFFIXES:
            raise ValueError(f"{target}: use one of {', '.join(SUFFIXES)}")
        document = self.config_document()
        text = dumps(document, target.suffix)
        partial = target.with_name(target.name + ".tmp")  # a crash mid-write leaves the old file
        partial.write_text(text)
        os.replace(partial, target)
        self.document = document  # what is on disk is now the baseline
        self.path = target
        self._changed.clear()
        return target

    # endregion


def resolve_live(path: str, root: Any) -> Any:
    """What a config field's `live` path points at in `root`, the object it is resolved against.

    The grammar: dot-separated keys walked from `root` (`output`,
    `stats.noise`, `readings.zone1.value`); a `*` segment fans out over
    every key at that level and yields a dict keyed by them
    (`outputs.*` -> `{"zone1": 603.7, ...}`, `readings.*.value`). None when
    a key is missing; a fan-out drops keys the rest of the path misses.
    """
    return _resolve(path.split(".") if path else [], root)


def _resolve(segments: list[str], node: Any) -> Any:
    if not segments:
        return node
    head, rest = segments[0], segments[1:]
    if not isinstance(node, dict):
        return None
    if head == "*":
        found = {key: _resolve(rest, value) for key, value in node.items()}
        return {key: value for key, value in found.items() if value is not None}
    return _resolve(rest, node.get(head))


def _tagged(config: Any) -> dict[str, Any]:
    """A link config as its file form: `tag` first, then its fields."""
    return {"tag": config.config_tag, **config.model_dump(mode="json", exclude_none=True)}


def dumps(document: dict[str, Any], suffix: str) -> str:
    """Serialise a document in the format `suffix` names."""
    match suffix.lower():
        case ".toml":
            import tomli_w

            return tomli_w.dumps(_without_none(document))
        case ".yaml" | ".yml":
            import yaml

            return yaml.safe_dump(document, sort_keys=False)
        case ".json":
            return json.dumps(document, indent=2) + "\n"
    raise ValueError(f"unknown document format {suffix!r}")


def _without_none(value: Any) -> Any:
    """TOML has no null: drop keys whose value is None, recursively."""
    if isinstance(value, dict):
        return {k: _without_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_without_none(v) for v in value]
    return value
