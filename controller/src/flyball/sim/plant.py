class Lag:
    """A first-order lag: the output chases the input with a time constant.

    ``dy/dt = (gain * u - y) / tau``, stepped exactly for a held input, so the
    step size does not change the trajectory. Most things a loop regulates
    look like this, or like two of these in series.
    """

    __slots__ = ("gain", "tau_s", "value")

    def __init__(self, tau_s: float, value: float = 0.0, gain: float = 1.0) -> None:
        if tau_s <= 0:
            raise ValueError("time constant must be positive")
        self.tau_s = tau_s
        self.value = value
        self.gain = gain

    def step(self, u: float, dt_s: float) -> float:
        """Hold ``u`` for ``dt_s`` seconds; return the new output."""
        from math import exp

        target = self.gain * u
        self.value = target + (self.value - target) * exp(-dt_s / self.tau_s)
        return self.value
