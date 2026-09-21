"""`Catalog`/`Catalogs`: what's installed, by tag -- explicit registration, no import magic."""

from __future__ import annotations

import pytest

from flyball.model.catalog import Catalog, Catalogs, current_catalog, get_catalog, set_catalog
from flyball.model.config import Config


@pytest.fixture
def kinds(fresh):
    a, b = fresh("link_a"), fresh("link_b")

    class A(Config[str], tag=a):
        def build(self) -> str:
            return "A"

    class B(Config[str], tag=b):
        def build(self) -> str:
            return "B"

    return a, b, A, B


class TestCatalog:
    def test_register_get_and_contains(self, kinds):
        a, _b, A, _B = kinds
        catalog = Catalog("link")
        assert a not in catalog and len(catalog) == 0
        catalog.register(A)
        assert a in catalog and catalog[a] is A and catalog.get(a) is A
        assert catalog.get("nope") is None and catalog.get("nope", A) is A
        assert catalog.tags() == [a] and dict(catalog.items()) == {a: A}

    def test_getitem_of_an_unregistered_tag_is_a_keyerror(self):
        catalog = Catalog("link")
        with pytest.raises(KeyError, match="link tag 'nope' is not registered"):
            catalog["nope"]

    def test_registering_the_same_class_twice_is_not_a_clash(self, kinds):
        a, _b, A, _B = kinds
        catalog = Catalog("link")
        catalog.register(A)
        catalog.register(A)  # idempotent: safe to `discover()` more than once
        assert catalog[a] is A

    def test_a_different_class_under_the_same_tag_is_refused(self, fresh):
        tag = fresh("dup")

        class A(Config[str], tag=tag):
            def build(self) -> str:
                return "A"

        class Again(Config[str], tag=tag):
            def build(self) -> str:
                return ""

        catalog = Catalog("link")
        catalog.register(A)
        with pytest.raises(ValueError, match="already"):
            catalog.register(Again)

    def test_register_takes_an_explicit_tag_override(self):
        class Untagged(Config[str]):
            def build(self) -> str:
                return ""

        catalog = Catalog("link")
        catalog.register(Untagged, tag="explicit")
        assert catalog["explicit"] is Untagged

    def test_register_with_no_tag_anywhere_is_refused(self):
        class Untagged(Config[str]):
            def build(self) -> str:
                return ""

        catalog = Catalog("link")
        with pytest.raises(ValueError, match="has no tag"):
            catalog.register(Untagged)

    def test_unregister_drops_a_tag_and_is_a_no_op_if_absent(self, kinds):
        a, _b, A, _B = kinds
        catalog = Catalog("link")
        catalog.register(A)
        catalog.unregister(a)
        assert a not in catalog
        catalog.unregister(a)  # no error


class TestCatalogs:
    def test_register_star_methods_route_to_the_right_kind(self, kinds):
        a, b, A, B = kinds
        catalog = Catalogs()
        catalog.register_link(A)
        catalog.register_link(B)
        assert catalog.links[a] is A and catalog.links[b] is B
        assert a not in catalog.devices

    def test_discover_calls_every_entry_point_s_register(self, monkeypatch):
        from importlib.metadata import EntryPoint

        calls = []

        class FakeModule:
            @staticmethod
            def register(catalog: Catalogs) -> None:
                calls.append(catalog)

        class Entry(EntryPoint):
            def load(self):
                return FakeModule

        entries = [Entry("acme", "acme.configs", "flyball.configs")]
        monkeypatch.setattr(
            "importlib.metadata.entry_points",
            lambda group: entries if group == "flyball.configs" else [],
        )
        catalog = Catalogs()
        assert catalog.discover() == ["acme"] and calls == [catalog]
        assert catalog.discover("other") == []

    def test_discover_raises_loudly_when_an_entry_point_has_no_register(self, monkeypatch):
        """The bluesky/qcodes/pymeasure regression this whole mechanism exists to catch."""
        from importlib.metadata import EntryPoint

        class BrokenModule:
            """No `register` function: exactly what shipped before the fix."""

        class Entry(EntryPoint):
            def load(self):
                return BrokenModule

        entries = [Entry("broken", "broken.configs", "flyball.configs")]
        monkeypatch.setattr(
            "importlib.metadata.entry_points",
            lambda group: entries if group == "flyball.configs" else [],
        )
        with pytest.raises(AttributeError, match="register"):
            Catalogs().discover()


class TestCurrentCatalog:
    def test_set_current_and_get(self):
        """The suite's own `_catalog` session fixture already sets one; save and restore it."""
        before = current_catalog()
        set_catalog(None)
        try:
            assert current_catalog() is None
            with pytest.raises(RuntimeError, match="no Catalogs is set"):
                get_catalog()
            catalog = Catalogs()
            set_catalog(catalog)
            assert current_catalog() is catalog and get_catalog() is catalog
        finally:
            set_catalog(before)
