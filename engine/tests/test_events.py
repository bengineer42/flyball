"""`/api/events` and `/ws/events`: the recent events, read from a snapshot, with checked queries."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

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
