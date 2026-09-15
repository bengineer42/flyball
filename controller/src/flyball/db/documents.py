"""A session as Bluesky event-model documents.

The event model is what the synchrotron world's analysis tools read:
a `start` (the run and its metadata), one `descriptor` per stream (its
data keys, with dtype, shape, units, precision), an `event` per point, a
`stop`. A recorded session maps onto it directly -- the session is the
run, each source is a stream, each sample an event, each loop a second
stream of ticks -- so this module walks the store and yields `(name, doc)`
pairs, the shape `bluesky.callbacks` and databroker consume, and can write
them as JSON lines.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .store import Store

Document = tuple[str, dict[str, Any]]

TICK_KEYS = ("reading", "setpoint", "correction", "demand", "expected", "delivered_correction")


def documents(store: Store, session_id: int) -> Iterator[Document]:
    """The session as `(name, document)` pairs, in order."""
    session = store.session(session_id)
    start_uid = str(uuid.uuid4())
    start_s = session.start_ns / 1e9
    sources = store.sources(session_id)
    channels = store.channels(session_id)
    loops = store.loops(session_id)

    yield (
        "start",
        {
            "uid": start_uid,
            "time": start_s,
            "plan_name": "flyball",
            "detectors": [s.name for s in sources],
            "flyball": {
                "session": session.id,
                "version": session.version,
                "config": session.config,
                "hardware": session.hardware,
                "details": session.details,
            },
        },
    )

    counts: dict[str, int] = {}
    for source in sources:
        keys = {
            c.name: {
                "source": f"flyball:{c.name}",
                "dtype": "number",
                "shape": [],
                "units": c.measurand.unit,
            }
            for c in channels
            if c.source.name == source.name
        }
        descriptor_uid = str(uuid.uuid4())
        yield (
            "descriptor",
            {
                "uid": descriptor_uid,
                "run_start": start_uid,
                "time": start_s,
                "name": source.name,
                "data_keys": keys,
                "object_keys": {source.name: list(keys)},
            },
        )
        n = 0
        for sample in store.samples(session_id, source.name):
            n += 1
            time_s = (session.start_ns + sample.offset_ns) / 1e9
            data = {f"{source.name}.{m}": v for m, v in sample.values.items()}
            yield (
                "event",
                {
                    "uid": str(uuid.uuid4()),
                    "descriptor": descriptor_uid,
                    "time": time_s,
                    "seq_num": sample.seq,
                    "data": data,
                    "timestamps": dict.fromkeys(data, time_s),
                },
            )
        counts[source.name] = n

    for loop in loops:
        stream = f"loop:{loop.name}"
        unit = loop.channel.measurand.unit
        keys = {
            f"{stream}.{key}": {
                "source": f"flyball:{stream}.{key}",
                "dtype": "number",
                "shape": [],
                "units": unit,
            }
            for key in TICK_KEYS
        }
        descriptor_uid = str(uuid.uuid4())
        yield (
            "descriptor",
            {
                "uid": descriptor_uid,
                "run_start": start_uid,
                "time": start_s,
                "name": stream,
                "data_keys": keys,
                "object_keys": {stream: list(keys)},
                "configuration": {
                    stream: {"data": {"law": loop.config}, "data_keys": {}, "timestamps": {}}
                },
            },
        )
        n = 0
        for tick in store.ticks(session_id, loop.name):
            n += 1
            time_s = (session.start_ns + tick.offset_ns) / 1e9
            data = {
                f"{stream}.{key}": value
                for key in TICK_KEYS
                if (value := getattr(tick, key)) is not None
            }
            yield (
                "event",
                {
                    "uid": str(uuid.uuid4()),
                    "descriptor": descriptor_uid,
                    "time": time_s,
                    "seq_num": n,
                    "data": data,
                    "timestamps": dict.fromkeys(data, time_s),
                },
            )
        counts[stream] = n

    yield (
        "stop",
        {
            "uid": str(uuid.uuid4()),
            "run_start": start_uid,
            "time": (session.end_ns if session.end_ns is not None else session.start_ns) / 1e9,
            "exit_status": "success" if session.end_ns is not None else "abort",
            "reason": "" if session.end_ns is not None else "session still open",
            "num_events": counts,
        },
    )


def write_jsonl(store: Store, session_id: int, path: str | Path) -> int:
    """Write the session's documents as JSON lines, `{"name": ..., "doc": ...}` each.

    Returns:
        How many were written.
    """
    count = 0
    with Path(path).open("w") as out:
        for name, doc in documents(store, session_id):
            out.write(json.dumps({"name": name, "doc": doc}) + "\n")
            count += 1
    return count
