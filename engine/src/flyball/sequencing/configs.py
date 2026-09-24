"""`sequencing`'s own `flyball.configs` entry point: the built-in commands.

Same reasoning as `flyball.control.configs`: a command used to register
itself by the side effect of its module being imported (`flyball.sequencing`
always pulling in `devices`/`loops`/`activities`); explicit is no worse for
something guaranteed to be present, and it is the only mechanism third-party
program steps get too.
"""

from flyball.model.catalog import Catalogs

from .activities import Prompt
from .devices import RunCommand, Set
from .loops import Manual, Ramp, Regulate, Settle, Wait


def register(catalog: Catalogs) -> None:
    catalog.register_step(Prompt)
    catalog.register_step(Set)
    catalog.register_step(RunCommand)
    catalog.register_step(Regulate)
    catalog.register_step(Ramp)
    catalog.register_step(Wait)
    catalog.register_step(Settle)
    catalog.register_step(Manual)
