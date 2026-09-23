"""`/docs` works with no internet: Swagger UI is served by the runner, not a CDN."""

from __future__ import annotations

import re

import pytest

from conftest import TestClient
from flyball.interfaces.server import create_app


def _urls(html: str) -> list[str]:
    return re.findall(r"""(?:src|href|url)\s*[=:]\s*['"]([^'"]+)['"]""", html)


@pytest.mark.parametrize("root", [None, "/flyball/oven"])
def test_docs_reference_no_other_host_and_their_assets_are_served(root):
    prefix = root or ""
    with TestClient(create_app(root_path=root)) as http:
        page = http.get(f"{prefix}/docs")
        assert page.status_code == 200
        assert "://" not in page.text, "every URL on the page is the runner's own"
        urls = _urls(page.text)
        assert any(u.endswith("swagger-ui-bundle.js") for u in urls), urls
        assert any(u.endswith("swagger-ui.css") for u in urls), urls
        for url in urls:
            assert url.startswith(prefix + "/"), url
            assert http.get(url).status_code == 200, url


def test_redoc_is_gone_rather_than_loaded_from_a_cdn():
    with TestClient(create_app()) as http:
        assert http.get("/redoc").status_code == 404
