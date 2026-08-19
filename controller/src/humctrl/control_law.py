from typing import Protocol


class ControlLaw(Protocol):
    def step(self, time: float, reading: float, set_point: float) -> float: ...
    def record_state(self): ...


class PController(ControlLaw):
    kp: float = 0

    def __init__(self, kp: float = 0):
        self.kp = kp

    def step(self, time: float, reading: float, set_point: float) -> float:
        error = set_point - reading
        output = self.kp * error
        return output


class PIController(ControlLaw):
    kp: float = 0
    ki: float = 0

    integral: float = 0.0
    last_time: float = None

    def __init__(self, kp: float = 0, ki: float = 0):
        self.kp = kp
        self.ki = ki

    def step(self, time: float, reading: float, set_point: float) -> float:
        error = set_point - reading
        if self.last_time is not None:
            dt = time - self.last_time
            assert dt > 0, ValueError("Time must be increasing")
            self.integral += error * dt

        output = self.kp * error + self.ki * self.integral

        self.last_time = time

        return output


class PIDController(ControlLaw):
    kp: float = 0
    ki: float = 0
    kd: float = 0

    integral: float = 0.0
    last_error: float = 0.0
    last_time: float = None

    def __init__(self, kp: float = 0, ki: float = 0, kd: float = 0):
        self.kp = kp
        self.ki = ki
        self.kd = kd

    def step(self, time: float, reading: float, set_point: float) -> float:
        error = set_point - reading
        derivative = 0.0
        if self.last_time is not None:
            dt = time - self.last_time
            assert dt > 0, ValueError("Time must be increasing")
            self.integral += error * dt
            derivative = (error - self.last_error) / dt

        output = self.kp * error + self.ki * self.integral + self.kd * derivative

        self.last_error = error
        self.last_time = time

        return output
