# flyball

Python control library for a split-range humidity rig: wet and dry air lines
mixed to a target relative humidity.

Design and hardware context: [../HANDOFF.md](../HANDOFF.md).

Nothing here has been tested against hardware.

```sh
uv sync --all-extras          # dev environment
make check                    # ruff, import-linter, pyright, pytest
make test                     # just the suite
```
