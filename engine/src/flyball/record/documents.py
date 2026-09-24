"""A session as Bluesky event-model documents.

The event model is what the synchrotron world's analysis tools read:
a `start` (the run and its metadata), one `descriptor` per stream (its
data keys, with dtype, shape, units, precision), an `event` per point, a
`stop`. A recorded session maps onto it directly -- the session is the
run, each device is a stream, each sample an event, each controller a
second stream of ticks -- so this module walks the store and yields `(name, doc)`
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

TICK_KEYS = (
    "measured_value",
    "setpoint",
    "correction",
    "output_value",
    "expected",
    "delivered_correction",
)


def _descriptor_dtype(dtype: str, hint: Any = None) -> str:
    """A signal's wire `dtype` as a Bluesky-style descriptor dtype.

    `hint` is a decoded value seen for the signal, used only to tell a
    `json` dtype's dict from its list -- the store does not carry that.
    """
    if dtype in ("float", "int"):
        return "number"
    if dtype == "bool":
        return "boolean"
    if dtype in ("str", "enum"):
        return "string"
    if dtype == "json":
        return "object" if isinstance(hint, dict) else "array"
    return "string"


def documents(store: Store, session_id: int) -> Iterator[Document]:
    """The session as `(name, document)` pairs, in order."""
    session = store.session(session_id)
    start_uid = str(uuid.uuid4())
    start_s = session.start_ns / 1e9
    devices = store.devices(session_id)
    signals = store.signals(session_id)
    controllers = store.controllers(session_id)

    yield (
        "start",
        {
            "uid": start_uid,
            "time": start_s,
            "plan_name": "flyball",
            "detectors": [d.address for d in devices],
            "flyball": {
                "session": session.id,
                "flyball_version": session.flyball_version,
                "config": session.config,
                "hardware": session.hardware,
                "details": session.details,
            },
        },
    )

    counts: dict[str, int] = {}
    for device in devices:
        samples = store.samples(session_id, device.address)
        hints: dict[str, Any] = {}
        for sample in samples:
            for address, value in sample.values.items():
                if value is not None:  # a reading with no value says nothing of the type
                    hints.setdefault(address, value)
        keys = {
            s.address: {
                "source": f"flyball:{s.address}",
                "dtype": _descriptor_dtype(s.dtype, hints.get(s.address)),
                "shape": s.shape,
                "units": s.unit,
            }
            for s in signals
            if s.device_id == device.id
        }
        descriptor_uid = str(uuid.uuid4())
        yield (
            "descriptor",
            {
                "uid": descriptor_uid,
                "run_start": start_uid,
                "time": start_s,
                "name": device.address,
                "data_keys": keys,
                "object_keys": {device.address: list(keys)},
            },
        )
        n = 0
        for sample in samples:
            n += 1
            time_s = (session.start_ns + sample.offset_ns) / 1e9
            data = dict(sample.values)
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
        counts[device.address] = n

    units = {s.address: s.unit for s in signals}
    for controller in controllers:
        stream = f"controller:{controller.name}"
        unit = units.get(controller.measured, "")
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
                    stream: {"data": {"law": controller.law}, "data_keys": {}, "timestamps": {}}
                },
            },
        )
        n = 0
        for tick in store.ticks(session_id, controller.name):
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
    with Path(path).open("w", encoding="utf-8") as out:
        for name, doc in documents(store, session_id):
            out.write(json.dumps({"name": name, "doc": doc}) + "\n")
            count += 1
    return count
