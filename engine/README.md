# flyball

The Python package behind flyball: devices, signals, controllers, programs, the
recorder and the runner that serves a rig over HTTP, websockets and MCP. The
book is at [bengineer42.github.io/flyball](https://bengineer42.github.io/flyball/latest/);
the root [README](../README.md) is the short version.

The control loop has been run on real hardware: a Raspberry Pi regulating chamber
humidity (I2C SHT4x sensors, PWM-driven pumps, closed-loop PI control). See
[humctrl](https://github.com/bengineer42/humctrl), the reference application.

```sh
uv sync --all-extras          # dev environment
make check                    # ruff, import-linter, pyright, pytest
make test                     # just the suite
```
