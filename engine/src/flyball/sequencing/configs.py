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
    catalog.register_command(Prompt)
    catalog.register_command(Set)
    catalog.register_command(RunCommand)
    catalog.register_command(Regulate)
    catalog.register_command(Ramp)
    catalog.register_command(Wait)
    catalog.register_command(Settle)
    catalog.register_command(Manual)
