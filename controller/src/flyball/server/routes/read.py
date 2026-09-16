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
from pydantic import BaseModel

from flyball.core.signal import Reading, Sample
from flyball.server.deps import RigDep
from flyball.server.schemas import ReadingOut, SampleOut

router = APIRouter(prefix="/api/read", tags=["read"])


class ReadOut(BaseModel):
    """One of the three, by what the address named; the other two are left out."""

    reading: ReadingOut | None = None
    sample: SampleOut | None = None
    samples: list[SampleOut] | None = None

    @classmethod
    def of(cls, result: Reading | Sample | Iterator[Sample] | Any) -> ReadOut:
        if isinstance(result, Reading):
            return cls(reading=ReadingOut.of(result))
        if isinstance(result, Sample):
            return cls(sample=SampleOut.of(result))
        return cls(samples=[SampleOut.of(s) for s in result])


@router.get("", response_model_exclude_none=True)
def read_many(
    rig: RigDep,
    at: Annotated[str, Query(description="Addresses, comma-separated.")],
    fresh: bool = False,
) -> list[ReadOut]:
    """Several addresses at once, in the order given; a fresh read hits each device once."""
    targets = [rig.resolve(address.strip()) for address in at.split(",") if address.strip()]
    return [ReadOut.of(result) for result in rig.read(targets, fresh=fresh)]


@router.get("/{address}", response_model_exclude_none=True)
def read_one(rig: RigDep, address: str, fresh: bool = False) -> ReadOut:
    """What the address names: `reading`, `sample` or `samples`. 503 until the first read."""
    return ReadOut.of(rig.read(rig.resolve(address), fresh=fresh))
