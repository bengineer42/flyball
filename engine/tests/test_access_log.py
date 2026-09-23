"""uvicorn's access log writes the path with its query: a `?token=` must not land in the file."""

from __future__ import annotations

import logging

from flyball.interfaces.server import create_app


def test_the_access_log_keeps_the_path_and_drops_the_query(caplog):
    create_app()
    with caplog.at_level(logging.INFO):
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:5000",
            "GET",
            "/api/export/1.csv?token=s3cret&every=10",
            "1.1",
            200,
        )
        logging.getLogger("uvicorn.error").info(
            '%s - "WebSocket %s" [accepted]', "127.0.0.1:5000", "/ws/samples?token=s3cret"
        )
        logging.getLogger("uvicorn.error").info("Started server process [%d]", 42)
    assert "s3cret" not in caplog.text
    assert '"GET /api/export/1.csv?… HTTP/1.1" 200' in caplog.text
    assert '"WebSocket /ws/samples?…" [accepted]' in caplog.text
    assert "Started server process [42]" in caplog.text, "other lines untouched"


def test_it_is_installed_once_however_many_apps(caplog):
    create_app()
    create_app()
    access = logging.getLogger("uvicorn.access")
    assert sum(type(f).__name__ == "QueryRedaction" for f in access.filters) == 1
