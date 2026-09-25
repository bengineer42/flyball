"""Sessions and what produced them: the store's provenance columns."""

from __future__ import annotations

from flyball.record import SqliteStore


def test_a_session_keeps_its_packages_and_a_kept_range_copies_them(tmp_path):
    store = SqliteStore(tmp_path / "s.sqlite")
    try:
        packages = {"flyball": "0.1.0", "flyball-linux": "0.2.0"}
        writer = store.open_session(0, flyball_version="0.1.0", packages=packages)
        session = writer.session
        assert session.packages == packages and store.session(session.id).packages == packages
        writer.end(10)
        kept = store.keep_range(session.id, 2, 8)
        assert (kept.flyball_version, kept.packages) == ("0.1.0", packages)
        assert store.open_session(0).session.packages is None, "none said: none kept"
    finally:
        store.close()
