from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Channel:
    source: str
    quantity: str
    units: str | None = None


@dataclass(frozen=True, slots=True)
class Reading:
    time_ns: int
    value: float
    channel: Channel

    @property
    def seconds(self) -> float:
        return self.time_ns / 1e9

    @property
    def source(self) -> str:
        return self.channel.source

    @property
    def quantity(self) -> str:
        return self.channel.quantity

    @property
    def units(self) -> str | None:
        return self.channel.units
