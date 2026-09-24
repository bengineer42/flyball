"""Reading by address: a signal's last reading, a node's sample, a device's samples.

`GET /api/read/{address}` answers with whatever the address names -- a
`reading` for a signal, a `sample` for an atomic namespace, `samples` for a
device or a namespace read over several transactions -- and `?fresh=true`
reads the hardware first, which is how a setting (`RW`, never published) is
read. `GET /api/read?at=a,b,c` reads several at once, one device read per
device.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, SerializerFunctionWrapHandler, model_serializer

from flyball.foundation.device import Reading, Sample
from flyball.foundation.errors import NotReadyError
from flyball.interfaces.server.deps import RigDep
from flyball.interfaces.server.schemas import ReadingOut, SampleOut
from flyball.rig import Rig

router = APIRouter(prefix="/api/read", tags=["read"])


class ReadOut(BaseModel):
    """One of the three, by what the address named; the other two are left out.

    A reading with no value keeps its `value: null`, with its `quality` and `reason`, the
    newest reading that had one (`last_usable`) and its `age_s`.
    """

    reading: ReadingOut | None = None
    sample: SampleOut | None = None
    samples: list[SampleOut] | None = None

    @model_serializer(mode="wrap")
    def _one(self, handler: SerializerFunctionWrapHandler):
        """Only the one the address named: the other two are left out, not null."""
        return {k: v for k, v in handler(self).items() if v is not None}

    @classmethod
    def of(cls, result: Reading | Sample | Iterator[Sample] | Any, rig: Rig) -> ReadOut:
        if isinstance(result, Reading):
            usable = rig.router.last_usable.get(result.signal)
            return cls(reading=ReadingOut.of(result, usable, rig.clock.now_ns()))
        if isinstance(result, Sample):
            return cls(sample=SampleOut.of(result))
        return cls(samples=[SampleOut.of(s) for s in result])


@router.get("")
def read_many(
    rig: RigDep,
    at: Annotated[str, Query(description="Addresses, comma-separated.")],
    fresh: bool = False,
) -> list[ReadOut | None]:
    """Several addresses at once, in the order given; a fresh read hits each device once.

    An address nothing has been read on yet is `null` in its place, not a
    failure of the batch.
    """
    targets = [rig.resolve(address.strip()) for address in at.split(",") if address.strip()]
    if fresh:
        return [ReadOut.of(result, rig) for result in rig.read(targets, fresh=True)]
    out: list[ReadOut | None] = []
    for target in targets:
        try:
            out.append(ReadOut.of(rig.read(target), rig))
        except NotReadyError:
            out.append(None)
    return out


@router.get("/{address}")
def read_one(rig: RigDep, address: str, fresh: bool = False) -> ReadOut:
    """What the address names: `reading`, `sample` or `samples`. 503 until the first read.

    A signal whose newest reading has no value answers `value: null` with its `quality`,
    `reason`, `last_usable` and `age_s`: not an error.
    """
    return ReadOut.of(rig.read(rig.resolve(address), fresh=fresh), rig)
