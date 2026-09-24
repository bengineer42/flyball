"""Tagged configs and their discriminated unions."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from flyball.foundation.config import Config, import_object, resolve
from flyball.model.config import INSTRUMENT_PACKAGES_ENV

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "simulated"


@pytest.fixture
def kinds(fresh):
    a, b = fresh("link_a"), fresh("link_b")

    class A(Config[str], type=a):
        x: int = 1

        def build(self) -> str:
            return f"A{self.x}"

    class B(Config[str], type=b):
        y: str

        def build(self) -> str:
            return f"B{self.y}"

    return a, b, A, B


def test_union_picks_the_config_by_tag_and_builds_it(kinds):
    a, b, A, B = kinds
    adapter = TypeAdapter(Config.union(A, B))
    assert adapter.validate_python({"type": a, "x": 2}).build() == "A2"
    assert adapter.validate_python({"type": b, "y": "hi"}).build() == "Bhi"
    with pytest.raises(ValidationError):
        adapter.validate_python({"x": 2})  # no tag: no guess
    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "nope"})


def test_schema_declares_the_discriminator(kinds):
    a, b, A, B = kinds
    schema = TypeAdapter(Config.union(A, B)).json_schema()
    assert schema["discriminator"]["propertyName"] == "type"
    assert {m.get("$ref", "").rsplit("/", 1)[-1] for m in schema["oneOf"]} == {"ATagged", "BTagged"}


def test_code_never_passes_the_tag(kinds):
    a, b, A, B = kinds
    assert A().build() == "A1" and A.type_name == a
    assert resolve(A(x=3)) == "A3" and resolve("already built") == "already built"


def test_duplicate_tag_is_refused_by_a_catalog(kinds):
    """Defining two `Config`s with the same tag is fine; a `Catalog` refuses to hold both."""
    from flyball.model.catalog import Catalog

    a, _b, A, _B = kinds

    class Again(Config[str], type=a):
        def build(self) -> str:
            return ""

    catalog = Catalog("link")
    catalog.register(A)
    with pytest.raises(ValueError, match="already"):
        catalog.register(Again)


def test_untagged_config_cannot_join_a_union():
    class Plain(Config[str]):
        def build(self) -> str:
            return ""

    with pytest.raises(TypeError, match="has no type"):
        Plain.tagged()


class TestResolveDocumentsAndLoadRigConfig:
    """`flyball.runtime.config`'s multi-file entry points: layer, then apply the board once."""

    def test_resolve_documents_merges_two_files_in_command_line_order(self, tmp_path):
        from flyball.runtime.config import resolve_documents

        (tmp_path / "a.yaml").write_text("name: a\nlinks: {l1: {type: sim_plant}}\n")
        (tmp_path / "b.yaml").write_text("name: b\n")
        document, files = resolve_documents([tmp_path / "a.yaml", tmp_path / "b.yaml"])
        assert document == {"name": "b", "links": {"l1": {"type": "sim_plant"}}}
        assert files == [tmp_path / "a.yaml", tmp_path / "b.yaml"]

    def test_board_is_looked_up_relative_to_the_first_file(self, tmp_path, monkeypatch):
        from flyball.runtime.config import BOARDS_ENV, resolve_documents

        monkeypatch.setenv(BOARDS_ENV, str(tmp_path / "profiles"))
        (tmp_path / "profiles").mkdir()
        (tmp_path / "profiles" / "test.toml").write_text(
            'name = "Test board"\n[links.bus]\ntype = "fake_registers"\n'
        )
        (tmp_path / "rig").mkdir()
        (tmp_path / "rig" / "a.yaml").write_text("board: test\nname: a\n")
        (tmp_path / "overlay.yaml").write_text("name: b\n")
        document, files = resolve_documents([
            tmp_path / "rig" / "a.yaml",
            tmp_path / "overlay.yaml",
        ])
        assert document["name"] == "b" and document["links"] == {"bus": {"type": "fake_registers"}}
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

    def test_a_legacy_section_is_refused_naming_the_reference(self, tmp_path):
        from flyball.runtime.config import load_rig_config

        (tmp_path / "old.yaml").write_text("readers:\n  - device: {type: x}\n")
        with pytest.raises(
            ValueError,
            match="readers/actuators/loops are no longer rig-file sections; devices and"
            r" controllers replace them, see book/src/7-reference/rig-file\.md",
        ):
            load_rig_config(tmp_path / "old.yaml")


class TestImportObject:
    """A rig file's dotted class path: only a subclass of `base`, only from under `allowed`."""

    def test_a_subclass_under_the_prefix_is_returned(self):
        import json.decoder

        assert (
            import_object("json.decoder.JSONDecoder", allowed=["json"], base="json.JSONDecoder")
            is json.decoder.JSONDecoder
        )

    @pytest.mark.parametrize("dotted", ["os.system", "subprocess.run", "builtins.exec", "jsonx.A"])
    def test_anything_outside_the_prefix_is_refused_before_it_is_imported(self, dotted):
        with pytest.raises(ValueError, match=r"is not a class from json\.: only those"):
            import_object(dotted, allowed=["json"], base="json.JSONDecoder")

    @pytest.mark.parametrize("dotted", ["json", "json..x", "json.decoder.", " json.x", "json.x-y"])
    def test_a_path_that_is_not_dotted_is_refused(self, dotted):
        with pytest.raises(ValueError, match="is not a dotted path to a class"):
            import_object(dotted, allowed=["json"], base="json.JSONDecoder")

    def test_under_the_prefix_it_must_be_a_subclass_of_base(self):
        with pytest.raises(ValueError, match=r"'json.dumps' is not a subclass of json.JSONDecoder"):
            import_object("json.dumps", allowed=["json"], base="json.JSONDecoder")
        with pytest.raises(ValueError, match="'json.decoder.Nope': json.decoder has no 'Nope'"):
            import_object("json.decoder.Nope", allowed=["json"], base="json.JSONDecoder")
        with pytest.raises(ValueError, match="cannot import json.nope"):
            import_object("json.nope.X", allowed=["json"], base="json.JSONDecoder")

    def test_a_base_that_is_not_installed_says_so(self):
        with pytest.raises(ValueError, match="cannot import no_such_library"):
            import_object("json.decoder.JSONDecoder", allowed=["json"], base="no_such_library.X")

    def test_more_packages_come_from_the_environment_and_still_need_the_base(self, monkeypatch):
        import email.parser

        monkeypatch.setenv(INSTRUMENT_PACKAGES_ENV, " email.parser , other.")
        assert (
            import_object("email.parser.Parser", allowed=["json"], base="email.parser.Parser")
            is email.parser.Parser
        )
        monkeypatch.setenv(INSTRUMENT_PACKAGES_ENV, "os")
        with pytest.raises(ValueError, match="'os.system' is not a subclass of json.JSONDecoder"):
            import_object("os.system", allowed=["json"], base="json.JSONDecoder")
