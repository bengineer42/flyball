from humctrl.control import Loop, Tuning


class Rig:
    loops: dict[str, Loop]
    tunings: dict[str, Tuning]
