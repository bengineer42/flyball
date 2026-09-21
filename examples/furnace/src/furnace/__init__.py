"""Registers this package's tagged config: the multi-zone furnace plant.

Importing `furnace` -- directly, or through the `flyball.configs` entry
point `discover()` reads -- makes `sim_furnace` valid in a rig file.
"""

from __future__ import annotations

import furnace.sim as sim

__all__ = ["sim"]
