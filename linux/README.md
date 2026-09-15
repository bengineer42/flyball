# flyball-linux

Linux I/O for flyball rigs: I²C, SPI, GPIO, PWM and 1-Wire links, and the
devices on them. Nothing here is board-specific; which bus numbers a board
has is a profile in [../boards/](../boards/).

```sh
uv sync --all-extras          # dev environment; the extras are the bus libraries
make check                    # ruff, pyright, pytest -- all on fake buses
uv run flyball rig check examples/greenhouse.sim.toml
uv run flyball-linux probe    # what this machine has
```

Documentation: the book's "Boards and Linux I/O" page.
