"""Registers `sim_furnace`: a multi-zone [Furnace][furnace.plant.Furnace] link."""

from __future__ import annotations

from typing import Any

from flyball.foundation.config import Config
from pydantic import ConfigDict, Field

from .plant import Furnace


class FurnaceConfig(Config[Furnace], type="sim_furnace"):
    """A multi-zone furnace ([Furnace][furnace.plant.Furnace]).

    Ports: inputs `heaterN` (a power in W, full drive being `power_w`),
    outputs `zoneN` and `sample` (temperatures in °C).
    """

    model_config = ConfigDict(extra="forbid")

    zones: int = Field(default=3, ge=1)
    power_w: float | list[float] = 2000.0
    capacity_j_per_k: float | list[float] = 5000.0
    coupling_w_per_k: float = Field(default=5.0, ge=0)
    loss_w_per_k: float = Field(default=2.0, ge=0)
    emissivity: float = Field(default=0.8, ge=0, le=1)
    area_m2: float = Field(default=0.02, ge=0)
    ambient_c: float = Field(default=20.0, json_schema_extra={"live": "outputs.*"})
    sample_capacity_j_per_k: float = Field(default=800.0, gt=0)
    sample_coupling_w_per_k: float = Field(default=4.0, ge=0)
    sample_zone: int = Field(default=2, ge=1)
    sensor_lag_s: float = Field(default=3.0, ge=0)
    noise: float = Field(default=0.0, ge=0, json_schema_extra={"live": "stats.noise"})
    seed: int | None = None
    initial_c: float | None = Field(default=None, json_schema_extra={"live": "outputs.*"})

    def build(self) -> Furnace:
        return Furnace(**self.model_dump(exclude={"type"}))

    def retune(self, plant: Any) -> None:
        """Apply the parameters to a running furnace; its temperatures stay where they are."""
        if not isinstance(plant, Furnace) or plant.zones != self.zones:
            raise ValueError("the number of zones cannot change while it runs")
        fresh = self.build()
        for attr in (
            "power",
            "capacity",
            "coupling",
            "loss",
            "emissivity",
            "area",
            "ambient",
            "sample_capacity",
            "sample_coupling",
            "sample_zone",
            "sensor_lag_s",
            "noise",
        ):
            setattr(plant, attr, getattr(fresh, attr))
