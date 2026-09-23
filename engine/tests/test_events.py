"""`/api/events` and `/ws/events`: the recent events, read from a snapshot, with checked queries."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from conftest import TestClient
from flyball.foundation.device import Event, Level
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.routes import events as events_routes


@pytest.fixture
def client(rig) -> Iterator[TestClient]:
    set_rig(rig)
    with TestClient(create_app()) as c:
        yield c
    set_rig(None)


def _growing(rig) -> Event:
    """An event whose level, when compared, raises another: an event raised mid-filter."""
    pending = [True]

    class Growing(int):
        def __ge__(self, other: object) -> bool:
            if pending:
                pending.pop()
                rig.event(Level.INFO, "rig", "test", "meanwhile", "raised while filtering")
            return int(self) >= int(other)  # type: ignore[call-overload]

    return Event(0, Growing(Level.WARNING), "rig", "test", "growing", "compared")  # type: ignore[arg-type]


class TestSnapshot:
    @pytest.mark.filterwarnings("ignore:Pydantic serializer warnings")  # the int-level stand-in
    def test_an_event_raised_while_filtering_is_no_error(self, client, rig):
        rig.event(Level.INFO, "rig", "test", "first", "before")
        rig.recent.append(_growing(rig))
        response = client.get("/api/events")
        assert response.status_code == 200, response.text
        assert [e["kind"] for e in response.json()] == ["first", "growing"]
        assert rig.recent[-1].kind == "meanwhile", "it is there for the next read"

    def test_an_event_raised_while_the_socket_primes_is_no_error(self, client, rig, monkeypatch):
        rig.event(Level.INFO, "rig", "test", "first", "before")
        rig.event(Level.INFO, "rig", "test", "second", "before")
        pending = [True]
        event_out = events_routes.event_out

        def growing(event: Event) -> dict:
            if pending:
                pending.pop()
                rig.recent.append(Event(0, Level.INFO, "rig", "test", "meanwhile", "priming"))
            return event_out(event)

        monkeypatch.setattr(events_routes, "event_out", growing)
        with client.websocket_connect("/ws/events") as ws:
            primed = ws.receive_json()
        assert [e["kind"] for e in primed["events"]] == ["first", "second"]


class TestQuery:
    @pytest.mark.parametrize("level", ["warning", "WARNING"])
    def test_level_keeps_that_level_and_above(self, client, rig, level):
        rig.event(Level.INFO, "rig", "test", "info", "")
        rig.event(Level.WARNING, "rig", "test", "warning", "")
        rig.event(Level.ERROR, "rig", "test", "error", "")
        response = client.get("/api/events", params={"level": level})
        assert response.status_code == 200, response.text
        assert [e["kind"] for e in response.json()] == ["warning", "error"]

    @pytest.mark.parametrize(
        "query", ["level=bogus", "level=", "limit=0", "limit=-1", "limit=10001", "limit=x"]
    )
    def test_a_bad_query_is_a_422(self, client, rig, query):
        rig.event(Level.INFO, "rig", "test", "info", "")
        response = client.get(f"/api/events?{query}")
        assert response.status_code == 422, response.text

    def test_limit_keeps_the_newest(self, client, rig):
        for kind in ("a", "b", "c"):
            rig.event(Level.INFO, "rig", "test", kind, "")
        assert [e["kind"] for e in client.get("/api/events?limit=2").json()] == ["b", "c"]
