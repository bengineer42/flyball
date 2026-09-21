# Boards

A board profile is data, not code: which buses a machine has, as flyball
links, and what its header pins are called. A rig file names one with
`board = "rpi5"` and then refers to `pin = "GPIO18"` rather than a chip and
a line number.

Profiles are looked up, in order, in `$FLYBALL_BOARDS` (colon-separated), a
`boards/` directory beside the rig file or in any directory above it (this
one, for rig files in this repository), `~/.config/flyball/boards`, and
`/etc/flyball/boards`. `board = "./my-board.toml"` is a path relative to the
rig file.

A profile has `name`, `links` (exactly as in a rig file), and `pins`: a
label to the device fields it stands for, usually `{ link, line }` for GPIO
or `{ link, channel }` for PWM. Fields a device entry gives itself win over
the pin's.

`flyball-linux probe` prints what `/dev` and `/sys` actually have on the
machine it runs on, which tells you which profile fits or what to put in a
new one. Nothing here is loaded until a rig file asks for it.
