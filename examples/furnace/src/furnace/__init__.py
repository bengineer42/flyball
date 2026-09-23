"""The multi-zone furnace plant.

`furnace.configs.register` is the `flyball.configs` entry point that makes
`sim_furnace` a valid type in a rig file -- explicit, not a side effect of
importing this package.
"""

from __future__ import annotations

import furnace.sim as sim

__all__ = ["sim"]
