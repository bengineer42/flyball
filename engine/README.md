# flyball

Python control library for a split-range humidity rig: wet and dry air lines
mixed to a target relative humidity.

The control loop has been run on real hardware: a Raspberry Pi running the humidity
application (I2C SHT4x sensors, PWM-driven pumps, closed-loop PI control), with no
controller bugs found. See [humctrl](https://github.com/bengineer42/humctrl), the
reference application, for the worked example.

```sh
uv sync --all-extras          # dev environment
make check                    # ruff, import-linter, pyright, pytest
make test                     # just the suite
```
