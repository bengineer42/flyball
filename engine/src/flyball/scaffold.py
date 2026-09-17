"""A starting point for a device driver of your own: `flyball new NAME`.

The template is a complete module that imports, schema-checks and registers
a tag, so the generated file works in a rig file before a line is changed.
"""

from __future__ import annotations

import keyword
import re
from pathlib import Path
from string import Template

__all__ = ["render", "write"]

_DEVICE = Template('''"""${Title}: a device driver built for this rig.

Registered as `driver: "${tag}"`, so a rig file can declare one:

    devices:
      ${name}:
        driver: ${tag}
"""

from __future__ import annotations

from collections.abc import Iterator

from flyball.core.device import DriverConfig, Output, Readable, command
from flyball.core.quantity import Quantity
from flyball.core.signal import Node, Sample
from flyball.core.units import Unit


class ${Title}(Readable):
    """TODO: what this measures or drives, and how."""

    value = Output("value", "Value", Quantity("value", Unit.get("1")))

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """Called every `poll_s`; yield the signals due at `time_ns`."""
        yield self.sample(time_ns, value=0.0)  # TODO: read the hardware

    @command
    def reset(self) -> None:
        """TODO: something an operator or program can trigger."""
        self.value.push(0.0)


class ${Title}Config(DriverConfig[${Title}], tag="${tag}"):
    """The rig-file entry: `driver: ${tag}` (flat) or its settings under `config:` (layered)."""

    def build(self, name: str, label: str | None = None) -> ${Title}:
        return ${Title}(name, label)
''')


def _identifier(name: str) -> str:
    snake = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()
    if not snake or snake[0].isdigit() or keyword.iskeyword(snake):
        raise ValueError(f"{name!r} does not make a Python identifier")
    return snake


def render(name: str) -> str:
    """The module text for a device driver called `name`: the tag is `name`, the class CamelCase."""
    snake = _identifier(name)
    title = "".join(part.capitalize() for part in snake.split("_"))
    return _DEVICE.substitute(Title=title, tag=snake, name=snake)


def write(name: str, directory: str | Path = ".") -> Path:
    """Render into `<directory>/<name>.py`; refuses to overwrite."""
    path = Path(directory) / f"{_identifier(name)}.py"
    if path.exists():
        raise FileExistsError(path)
    path.write_text(render(name))
    return path
