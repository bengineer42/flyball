"""`sequencing`'s own `flyball.configs` entry point: the built-in commands.

Same reasoning as `flyball.control.configs`: a command used to register
itself by the side effect of its module being imported (`flyball.sequencing`
always pulling in `devices`/`loops`/`activities`); explicit is no worse for
something guaranteed to be present, and it is the only mechanism third-party
program steps get too.
"""

from flyball.model.catalog import Catalogs

from .activities import Wait
from .devices import RunCommand, Set
from .loops import Arrive, Hold, Manual, Ramp, Regulate
from .tuning import Tune


def register(catalog: Catalogs) -> None:
    catalog.register_command(Wait)
    catalog.register_command(Set)
    catalog.register_command(RunCommand)
    catalog.register_command(Regulate)
    catalog.register_command(Ramp)
    catalog.register_command(Hold)
    catalog.register_command(Arrive)
    catalog.register_command(Manual)
    catalog.register_command(Tune)
