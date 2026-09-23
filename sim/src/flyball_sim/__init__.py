"""Rigs with nothing plugged in.

Pieces for a rig with no hardware, none of which knows what a particular
application is simulating: [Plant][flyball_sim.plant.Plant] and
[MultiPlant][flyball_sim.plant.MultiPlant] models, the clocks that step them,
and the `sim_plant`/`sim_daq`/`sim_drive` devices a rig file declares. An
application composes these into its own simulator (`examples/furnace`'s
`Furnace`, [humctrl](https://github.com/bengineer42/humctrl)'s
`HumidityChamber`); `flyball.runtime` must never import this package
directly -- only through the `flyball.configs` entry point, the same seam
every other optional package uses.

The one exception is [Simulation][flyball_sim.simulation.Simulation]: it
*does* know what is simulated -- a rig whose every link is `sim_*`/`fake_*`
-- and `flyball.runner` imports it too, but only lazily, from inside the
function that builds one, and only once `flyball.runtime.config.is_simulated`
has already said the rig qualifies.
"""

from .clock import ScaledClock, SteppedClock
from .devices import (
    DaqPort,
    DrivePort,
    PlantConfig,
    SimDaq,
    SimDaqConfig,
    SimDrive,
    SimDriveConfig,
)
from .plant import Fopdt, Integrator, Lag, MultiPlant, Noisy, Plant, Port
from .simulation import Simulation

__all__ = [
    "DaqPort",
    "DrivePort",
    "Fopdt",
    "Integrator",
    "Lag",
    "MultiPlant",
    "Noisy",
    "Plant",
    "PlantConfig",
    "Port",
    "ScaledClock",
    "SimDaq",
    "SimDaqConfig",
    "SimDrive",
    "SimDriveConfig",
    "Simulation",
    "SteppedClock",
]
