"""Engine's own `flyball.configs` entry point: the 9 built-in laws.

Per `brain/tasks/registry-redesign.md`'s decision that engine registers its
own built-ins through the same mechanism as every extension -- no
special-cased "what's compiled in" path. Declared in `engine/pyproject.toml`'s
own `[project.entry-points."flyball.configs"]`.
"""

from flyball.model.catalog import Catalogs

from .laws import IMC, PI, PID, OnOff, OpenLoop, P, Scheduled, SlidingMode, SmithPredictor


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
