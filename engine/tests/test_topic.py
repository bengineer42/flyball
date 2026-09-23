"""``Latest`` cells and ``Topic`` fan-out."""

from __future__ import annotations

import asyncio
import threading

from flyball.foundation.router import Latest, Topic


def test_latest_reports_only_what_changed_since_a_version():
    cell: Latest[str, int] = Latest()
    cell.set("a", 1)
    cell.set("b", 2)
    version, changed = cell.changed_since(0)
    assert changed == {"a": 1, "b": 2}
    cell.set("a", 3)
    version2, changed2 = cell.changed_since(version)
    assert changed2 == {"a": 3} and version2 > version
    assert cell.changed_since(version2)[1] == {}


def test_latest_keeps_only_the_newest_value():
    cell: Latest[str, int] = Latest()
    for i in range(1000):
        cell.set("k", i)
    assert cell.changed_since(0)[1] == {"k": 999}


def test_latest_watch_counts_readers():
    cell: Latest[str, int] = Latest()
    assert not cell.watched
    with cell.watch():
        assert cell.watched
        with cell.watch():
            assert cell.watched
        assert cell.watched
    assert not cell.watched


def test_topic_publish_without_subscribers_is_a_no_op():
    topic: Topic[int] = Topic()
    assert not topic.subscribed
    topic.publish(1)  # nowhere to go, nothing raised


def test_topic_delivers_to_subscribers_and_drops_oldest():
    async def main() -> list[int]:
        topic: Topic[int] = Topic()
        with topic.subscribe(maxsize=2) as queue:
            for i in range(5):
                topic.publish(i)
            await asyncio.sleep(0)
            return [queue.get_nowait(), queue.get_nowait()]

    assert asyncio.run(main()) == [3, 4]


def test_a_key_stored_while_a_reader_asks_is_never_missed():
    """The writer's version bump and its store are one step to a reader on another thread.

    The key's hash stalls the writer between the two, as a preempted thread would.
    """
    inside, go = threading.Event(), threading.Event()

    class Slow:
        armed = True

        def __hash__(self) -> int:
            if Slow.armed:
                Slow.armed = False
                inside.set()
                go.wait(2.0)
            return 1

    cell: Latest[Slow, int] = Latest()
    key = Slow()
    writer = threading.Thread(target=cell.set, args=(key, 1), daemon=True)
    writer.start()
    assert inside.wait(1.0), "the writer is between its version bump and its store"
    asked: list[tuple[int, dict[Slow, int]]] = []
    reader = threading.Thread(target=lambda: asked.append(cell.changed_since(0)), daemon=True)
    reader.start()
    reader.join(0.1)
    go.set()
    writer.join(1.0)
    reader.join(1.0)
    [(version, changed)] = asked
    later = cell.changed_since(version)[1]
    assert {**changed, **later} == {key: 1}, "seen on the first ask or the next"
