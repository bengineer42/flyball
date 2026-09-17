"""``Latest`` cells and ``Topic`` fan-out."""

from __future__ import annotations

import asyncio

from flyball.core.topic import Latest, Topic


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
