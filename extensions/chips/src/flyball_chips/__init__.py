"""Chip drivers for flyball rigs: one module each, protocol-level and OS-agnostic.

Every driver here talks to its chip through a `flyball.hardware.{i2c,spi,gpio,uart}`
link protocol only -- never a real bus directly -- so it runs unmodified against
`extensions/linux`'s real buses or `flyball-sim`'s scripted fakes (`fake_i2c`/
`fake_spi`/`fake_gpio`/`fake_uart`), whichever a rig's `links:` section builds.

[flyball_chips.configs][].register explicitly registers every type; the
`flyball.configs` entry point calls it for `flyball rig check` and the
runner.
"""
