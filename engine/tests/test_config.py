"""Tagged configs and their discriminated unions."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from flyball.foundation.config import Config, resolve

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "simulated"


@pytest.fixture
def kinds(fresh):
    a, b = fresh("link_a"), fresh("link_b")

    class A(Config[str], tag=a):
        x: int = 1

        def build(self) -> str:
            return f"A{self.x}"

    class B(Config[str], tag=b):
        y: str

        def build(self) -> str:
            return f"B{self.y}"

    return a, b, A, B


def test_union_picks_the_config_by_tag_and_builds_it(kinds):
    a, b, A, B = kinds
    adapter = TypeAdapter(Config.union(A, B))
    assert adapter.validate_python({"tag": a, "x": 2}).build() == "A2"
    assert adapter.validate_python({"tag": b, "y": "hi"}).build() == "Bhi"
    with pytest.raises(ValidationError):
        adapter.validate_python({"x": 2})  # no tag: no guess
    with pytest.raises(ValidationError):
        adapter.validate_python({"tag": "nope"})


def test_schema_declares_the_discriminator(kinds):
    a, b, A, B = kinds
    schema = TypeAdapter(Config.union(A, B)).json_schema()
    assert schema["discriminator"]["propertyName"] == "tag"
    assert {m.get("$ref", "").rsplit("/", 1)[-1] for m in schema["oneOf"]} == {"ATagged", "BTagged"}


def test_code_never_passes_the_tag(kinds):
    a, b, A, B = kinds
    assert A().build() == "A1" and A.config_tag == a
    assert resolve(A(x=3)) == "A3" and resolve("already built") == "already built"


def test_duplicate_tag_is_refused(kinds):
    a, *_ = kinds
    with pytest.raises(ValueError, match="already"):

        class Again(Config[str], tag=a):
            def build(self) -> str:
                return ""


def test_untagged_config_cannot_join_a_union():
    class Plain(Config[str]):
        def build(self) -> str:
            return ""

    with pytest.raises(TypeError, match="has no tag"):
        Plain.tagged()


def test_discover_loads_every_entry_point_in_the_group(monkeypatch):
    """A package that declares `flyball.configs` has its module imported, which registers tags."""
    from importlib.metadata import EntryPoint

    from flyball.foundation import config as module

    imported = []

    class Entry(EntryPoint):
        def load(self):
            imported.append(self.value)
            return object()

    entries = [Entry("acme", "acme.configs", "flyball.configs")]
    monkeypatch.setattr(
        "importlib.metadata.entry_points",
        lambda group: entries if group == "flyball.configs" else [],
    )
    assert module.discover() == ["acme"] and imported == ["acme.configs"]
    assert module.discover("other") == []


class TestResolveDocumentsAndLoadRigConfig:
    """`flyball.runtime.config`'s multi-file entry points: layer, then apply the board once."""

    def test_resolve_documents_merges_two_files_in_command_line_order(self, tmp_path):
        from flyball.runtime.config import resolve_documents

        (tmp_path / "a.yaml").write_text("name: a\nlinks: {l1: {tag: sim_plant}}\n")
        (tmp_path / "b.yaml").write_text("name: b\n")
        document, files = resolve_documents([tmp_path / "a.yaml", tmp_path / "b.yaml"])
        assert document == {"name": "b", "links": {"l1": {"tag": "sim_plant"}}}
        assert files == [tmp_path / "a.yaml", tmp_path / "b.yaml"]

    def test_board_is_looked_up_relative_to_the_first_file(self, tmp_path, monkeypatch):
        from flyball.runtime.config import BOARDS_ENV, resolve_documents

        monkeypatch.setenv(BOARDS_ENV, str(tmp_path / "profiles"))
        (tmp_path / "profiles").mkdir()
        (tmp_path / "profiles" / "test.toml").write_text(
            'name = "Test board"\n[links.bus]\ntag = "fake_registers"\n'
        )
        (tmp_path / "rig").mkdir()
        (tmp_path / "rig" / "a.yaml").write_text("board: test\nname: a\n")
        (tmp_path / "overlay.yaml").write_text("name: b\n")
        document, files = resolve_documents([
            tmp_path / "rig" / "a.yaml",
            tmp_path / "overlay.yaml",
        ])
        assert document["name"] == "b" and document["links"] == {"bus": {"tag": "fake_registers"}}
        assert tmp_path / "profiles" / "test.toml" in files

    def test_load_rig_config_of_a_single_path_still_works(self):
        from flyball.runtime.config import load_rig_config

        config = load_rig_config(EXAMPLES / "oven.yaml")
        assert config.name == "oven" and set(config.devices) == {"thermocouple", "heater"}

    def test_load_rig_config_of_two_files_applies_the_overlay(self, tmp_path):
        from flyball.runtime.config import load_rig_config

        shutil.copy(EXAMPLES / "oven.yaml", tmp_path / "oven.yaml")
        (tmp_path / "sim.yaml").write_text("links:\n  chamber:\n    noise: 0.9\n")
        config = load_rig_config([tmp_path / "oven.yaml", tmp_path / "sim.yaml"])
        assert config.name == "oven" and config.links["chamber"].noise == 0.9
        assert config.links["chamber"].tau_s == 60.0, "the rest of the base link is untouched"

    def test_load_rig_config_applies_a_set_after_the_layers(self, tmp_path):
        from flyball.runtime.config import load_rig_config

        shutil.copy(EXAMPLES / "oven.yaml", tmp_path / "oven.yaml")
        config = load_rig_config([tmp_path / "oven.yaml"], ["links.chamber.noise=0.9"])
        assert config.links["chamber"].noise == 0.9

    def test_a_legacy_section_is_refused_naming_the_plan(self, tmp_path):
        from flyball.runtime.config import load_rig_config

        (tmp_path / "old.yaml").write_text("readers:\n  - device: {tag: x}\n")
        with pytest.raises(
            ValueError,
            match="readers/actuators/loops are no longer rig-file sections; devices and"
            r" controllers replace them, see temp-docs/DEVICE-MODEL-PLAN.md §6",
        ):
            load_rig_config(tmp_path / "old.yaml")
