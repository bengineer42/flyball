from typing import Self

from humctrl.typing import NonZero


class SetPointGenerator:
    def generate(self, time: float) -> float:
        raise NotImplementedError()

    @property
    def running(self) -> bool:
        return True


class LinearRamp(SetPointGenerator):
    def __init__(
        self,
        start_time: float,
        end_time: float,
        start: float,
        end: float,
    ) -> None:
        if end_time <= start_time:
            raise ValueError("end_time must be greater than start_time")
        self.start_time = start_time

        self.start = start
        self.end = end
        self.rate = (self.end - self.start) / (end_time - self.start_time)
        self.end_time = end_time

    def generate(self, time: float) -> float:
        elapsed = time - self.start_time
        if elapsed < 0:
            return self.start
        if elapsed >= self.end_time:
            return self.end
        return self.start + elapsed * self.rate

    @classmethod
    def from_duration(cls, start_time: float, duration: float, start: float, end: float) -> Self:
        end_time = start_time + duration
        return cls(start_time, end_time, start, end)

    @classmethod
    def from_rate(cls, start_time: float, rate: NonZero, start: float, end: float) -> Self:
        end_time = start_time + abs(end - start) / rate
        return cls(start_time, end_time, start, end)
