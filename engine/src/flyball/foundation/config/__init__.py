"""Compatibility shim: `Config`'s real home is now `flyball.model`.

`foundation/config/` was `core-split.md`'s reserved slot for `catalog.py`
before `model/` existed. `registry-redesign.md`/`engine-structure.md` moved
`config.py`/`model.py` out to the new top-level `model/` package instead (see
`tasks/engine-structure.md`'s layer 2) once `catalog.py` needed somewhere to
live alongside them -- `foundation/config/` was never meant to be permanent.

Kept as a re-export here (not deleted) because `flyball.foundation.config`
is, right now, the live import path for 65+ files across `engine`,
`extensions/*`, `sim` and `examples/*` -- the core-split's own import-rewrite
wave landed on this path for every one of them. Deleting it out from under
those in-flight/already-committed changes would break them all at once for
no functional gain. Import from `flyball.model` directly in new code; this
module is for existing call sites until a follow-up sweep repoints them and
removes this shim.
"""

from flyball.model.config import Config, ConfigOr, discover_paths, import_object, resolve
from flyball.model.model import ModelOf, creation_model, discriminated_union

__all__ = [
    "Config",
    "ConfigOr",
    "ModelOf",
    "creation_model",
    "discover_paths",
    "discriminated_union",
    "import_object",
    "resolve",
]
