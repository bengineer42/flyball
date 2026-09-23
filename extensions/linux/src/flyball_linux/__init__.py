"""Linux I/O for flyball: the buses a single-board computer exposes, and devices on them.

Nothing here is board-specific. I2C is `/dev/i2c-N`, SPI is `/dev/spidevN.M`,
GPIO is `/dev/gpiochipN`, PWM and 1-Wire are sysfs; a Raspberry Pi, a
BeagleBone or a USB bridge on a laptop all present the same files. Which
numbers a board uses is a board profile -- a data file on the board path --
not code.

Importing [flyball_linux.configs][] registers every type; the `flyball.configs`
entry point does that for `flyball rig check` and the runner.
"""
