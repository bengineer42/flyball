"""SCPI instruments over a text link: one query or command per signal.

`MEAS:VOLT:DC?` answers `+1.23456E-02`. A [Scpi][flyball_visa.Scpi]
device's tree is its own config: `channels` maps each signal's name to a
query, a write template, or both -- a generic driver's tree lives in its
own config, not the envelope. Replies parse as a float by
default; give `parse` for an instrument that answers `1.234 V`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping

from flyball.foundation.config import resolve
from flyball.foundation.device import (
    Access,
    Committable,
    DriverConfig,
    Node,
    Readable,
    Role,
    Sample,
    Signal,
    SignalSpec,
    command,
)
from flyball.foundation.quantities import Quantity
from flyball.hardware.links import TextLink
from flyball.hardware.scan import Scan
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ._links import TextLinkConfig

Parser = Callable[[str], float]


def parse_float(reply: str) -> float:
    """The default parser: the first token, as a number. `+1.2E-3 V` -> 0.0012."""
    return float(reply.strip().split()[0].rstrip(","))


class ScpiSignal(BaseModel):
    """One line of a `scpi` device's tree: a query, a write template, or both.

    `query` alone: an output, `[RP]`. `write`, with or without `query`: a
    demand, `[RPW]` -- its readback is the value last committed. Metadata
    such as range, precision and limits are not here -- they are the
    envelope's `signals:` overrides, which apply to any driver's
    tree.
    """

    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(
        default=None, description="A query; the reply is this signal's value."
    )
    write: str | None = Field(
        default=None, description="A command with `{value}` formatted in, sent on commit."
    )
    unit: str
    quantity: str | None = Field(
        default=None, description="The quantity's own name, if it differs from the signal's."
    )
    scale: float = Field(
        default=1.0, description="A reply or a demand is multiplied/divided by this."
    )

    @model_validator(mode="after")
    def _needs_query_or_write(self) -> ScpiSignal:
        if self.query is None and self.write is None:
            raise ValueError("a scpi signal needs `query`, `write`, or both")
        return self

    @property
    def role(self) -> Role:
        return Role.DEMAND if self.write is not None else Role.OUTPUT

    @property
    def access(self) -> Access:
        return Access.RPW if self.write is not None else Access.RP


class Scpi(Readable, Committable):
    """SCPI signals over a text link: each of `channels` becomes one signal.

    Args:
        name: The device's name.
        link: What to talk over.
        channels: `{name: ScpiSignal}` -- the tree, and how each signal reads or writes.
        parse: How a reply becomes a number.
    """

    def __init__(
        self,
        name: str,
        link: TextLink,
        channels: Mapping[str, ScpiSignal],
        parse: Parser = parse_float,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.parse = parse
        self.channels = dict(channels)
        # Device.blocking is a ClassVar; this driver's real bus or fake is only known
        # per instance, at build. The link itself says whether it wants the Writer
        # thread -- a real bus always does, a fake only if configured to.
        self.blocking = link.blocking  # pyright: ignore[reportAttributeAccessIssue]
        self._scan = Scan()
        self.bind([
            SignalSpec(
                name=key,
                quantity=Quantity(sig.quantity or key, sig.unit),
                access=sig.access,
                role=sig.role,
            )
            for key, sig in self.channels.items()
        ])

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """One query per due, publishing signal under `node`: each its own instant.

        A slow bus never claims two queries were simultaneous, so each
        yields its own [Sample][flyball.foundation.device.Sample] rather than one
        shared dict of values. Walks `channels`, not the tree: `conditions`
        and any `last.*` are in every device's tree now, and neither has a
        query behind it.
        """
        target = node if node is not None else self.root
        candidates = {
            self.signals[key]: key
            for key, channel in self.channels.items()
            if channel.query is not None and target.contains(self.signals[key])
        }
        for signal in self._scan.due(candidates, time_ns, whole=False):
            channel = self.channels[candidates[signal]]
            assert channel.query is not None
            value = self.parse(self.link.query(channel.query)) * channel.scale
            yield Sample(self.root, time_ns, {signal: value})

    def write_signal(self, signal: Signal, value: float) -> None:
        channel = self.channels[signal.name]
        assert channel.write is not None
        self.link.write(channel.write.format(value=value / channel.scale))

    @command
    def write(self, text: str) -> None:
        """Send any command. For bring-up, not programs."""
        self.link.write(text)

    @command
    def query(self, text: str) -> str:
        """Send any query and return the raw reply. For bring-up, not programs."""
        return self.link.query(text)


class ScpiConfig(DriverConfig[Scpi], tag="scpi"):
    """`driver: scpi`. `channels` is the driver's own tree -- see `ScpiSignal`.

    Named `channels`, not `signals`: the envelope's `signals:` key is
    reserved for overrides (range, precision, limits, ...), the same for
    every driver, so a driver's own config may not use that name.
    """

    link: TextLinkConfig | str  # type: ignore[valid-type]
    channels: dict[str, ScpiSignal]

    def build(self, name: str, label: str | None = None) -> Scpi:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a link before building")
        return Scpi(name, resolve(self.link), self.channels, label=label)


__all__ = ["Parser", "Scpi", "ScpiConfig", "ScpiSignal", "parse_float"]
