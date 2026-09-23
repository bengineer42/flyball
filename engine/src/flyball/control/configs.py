"""Engine's own `flyball.configs` entry point: the built-in laws, feedforwards and generators.

Engine registers its own built-ins through the same mechanism as every
extension -- no special-cased "what's compiled in" path. Declared in
`engine/pyproject.toml`'s own `[project.entry-points."flyball.configs"]`.
`Setpoint`/`NoFeedforward` live in `flyball.model.feedforward`, not here
(`Controller`'s own defaults, needed a layer below `control`), but are
registered from here too -- `model` is unordered in the layer contract,
reachable from everywhere, so importing them here is no different from
`control/feedforward.py` already re-exporting them for anyone importing the
base from its old home.
"""

from flyball.model.catalog import Catalogs
from flyball.model.feedforward import NoFeedforward, Setpoint

from .feedforward import Affine, Table
from .laws import IMC, PI, PID, OnOff, OpenLoop, P, Scheduled, SlidingMode, SmithPredictor
from .setpoint import Hold, LinearRampSetpoint, Profile


def register(catalog: Catalogs) -> None:
    catalog.register_law(OpenLoop)
    catalog.register_law(P)
    catalog.register_law(PI)
    catalog.register_law(PID)
    catalog.register_law(IMC)
    catalog.register_law(OnOff)
    catalog.register_law(SmithPredictor)
    catalog.register_law(Scheduled)
    catalog.register_law(SlidingMode)
    catalog.register_feedforward(Setpoint)
    catalog.register_feedforward(NoFeedforward)
    catalog.register_feedforward(Affine)
    catalog.register_feedforward(Table)
    catalog.register_generator(Hold)
    catalog.register_generator(LinearRampSetpoint)
    catalog.register_generator(Profile)
