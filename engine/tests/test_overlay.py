"""`merge`, `--set` parsing and application, and layering several files with `extends`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flyball.runtime.overlay import apply_set, merge, parse_set, resolve_layers


class TestMerge:
    def test_nested_mappings_deep_merge(self):
        assert merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 3, "z": 4}}) == {
            "a": {"x": 1, "y": 3, "z": 4}
        }

    def test_a_list_replaces_whole_not_concatenates(self):
        assert merge({"a": [1, 2, 3]}, {"a": [9]}) == {"a": [9]}

    def test_a_scalar_replaces(self):
        assert merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_an_explicit_none_deletes_the_key(self):
        assert merge({"a": 1, "b": 2}, {"a": None}) == {"b": 2}

    def test_deleting_a_key_the_base_does_not_have_is_a_no_op(self):
        assert merge({"a": 1}, {"b": None}) == {"a": 1}

    def test_neither_input_is_mutated(self):
        base = {"a": {"x": 1}}
        overlay = {"a": {"y": 2}, "b": None}
        merge(base, overlay)
        assert base == {"a": {"x": 1}}
        assert overlay == {"a": {"y": 2}, "b": None}

    def test_a_mapping_overlaying_a_scalar_replaces_it_rather_than_merging(self):
        assert merge({"a": 1}, {"a": {"x": 1}}) == {"a": {"x": 1}}


class TestParseSet:
    def test_splits_the_path_on_dots(self):
        path, value = parse_set("devices.furnace.noise=0.3")
        assert path == ["devices", "furnace", "noise"]
        assert value == 0.3 and isinstance(value, float)

    def test_types_a_bool(self):
        assert parse_set("a=true")[1] is True

    def test_types_null_as_none(self):
        assert parse_set("a=null")[1] is None

    def test_types_a_list(self):
        assert parse_set("a=[1, 2]")[1] == [1, 2]

    def test_a_bare_word_is_a_string(self):
        assert parse_set("a=hello")[1] == "hello"

    def test_no_equals_is_a_value_error(self):
        with pytest.raises(ValueError, match="KEY=VALUE"):
            parse_set("devices.furnace.noise")


class TestApplySet:
    def test_creates_intermediate_mappings(self):
        assert apply_set({}, ["a", "b", "c"], 1) == {"a": {"b": {"c": 1}}}

    def test_sets_an_existing_leaf(self):
        assert apply_set({"a": {"b": 1}}, ["a", "b"], 2) == {"a": {"b": 2}}

    def test_none_deletes_the_leaf(self):
        assert apply_set({"a": {"b": 1, "c": 2}}, ["a", "b"], None) == {"a": {"c": 2}}

    def test_deleting_a_missing_path_is_a_true_no_op(self):
        document = {"a": {"b": 1}}
        assert apply_set(document, ["a", "x", "y"], None) == document

    def test_does_not_mutate_its_input(self):
        document = {"a": {"b": 1}}
        apply_set(document, ["a", "c"], 2)
        assert document == {"a": {"b": 1}}

    def test_empty_path_is_a_value_error(self):
        with pytest.raises(ValueError):
            apply_set({}, [], 1)


class TestResolveLayers:
    def test_a_single_file_loads_as_is(self, tmp_path):
        (tmp_path / "a.yaml").write_text("name: a\n")
        document, files = resolve_layers([tmp_path / "a.yaml"])
        assert document == {"name": "a"}
        assert files == [tmp_path / "a.yaml"]

    def test_a_later_file_overlays_an_earlier_one(self, tmp_path):
        (tmp_path / "a.yaml").write_text("name: a\nlinks: {l1: {type: sim_plant}}\n")
        (tmp_path / "b.yaml").write_text("name: b\n")
        document, files = resolve_layers([tmp_path / "a.yaml", tmp_path / "b.yaml"])
        assert document == {"name": "b", "links": {"l1": {"type": "sim_plant"}}}
        assert files == [tmp_path / "a.yaml", tmp_path / "b.yaml"]

    def test_extends_is_applied_underneath_and_relative_to_the_extending_file(self, tmp_path):
        (tmp_path / "base").mkdir()
        (tmp_path / "base" / "b.yaml").write_text("name: base\nlinks: {l1: {type: sim_plant}}\n")
        (tmp_path / "top.yaml").write_text('extends: ["base/b.yaml"]\nname: top\n')
        document, files = resolve_layers([tmp_path / "top.yaml"])
        assert document == {"name": "top", "links": {"l1": {"type": "sim_plant"}}}
        assert "extends" not in document
        assert files == [tmp_path / "base" / "b.yaml", tmp_path / "top.yaml"]

    def test_the_command_line_order_beats_extends(self, tmp_path):
        (tmp_path / "base.yaml").write_text("name: base\n")
        (tmp_path / "top.yaml").write_text('extends: ["base.yaml"]\nname: top\n')
        (tmp_path / "last.yaml").write_text("name: last\n")
        # top.yaml extends base.yaml (base underneath top), then last.yaml overlays both.
        document, _ = resolve_layers([tmp_path / "top.yaml", tmp_path / "last.yaml"])
        assert document["name"] == "last"

    def test_a_cycle_is_refused(self, tmp_path):
        (tmp_path / "a.yaml").write_text('extends: ["b.yaml"]\n')
        (tmp_path / "b.yaml").write_text('extends: ["a.yaml"]\n')
        with pytest.raises(ValueError, match="cycle"):
            resolve_layers([tmp_path / "a.yaml"])

    def test_set_is_applied_last(self, tmp_path):
        (tmp_path / "a.yaml").write_text("links: {l1: {type: sim_plant, noise: 0.1}}\n")
        document, _ = resolve_layers(
            [tmp_path / "a.yaml"], ["links.l1.noise=0.5", "links.l1.seed=7"]
        )
        assert document == {"links": {"l1": {"type": "sim_plant", "noise": 0.5, "seed": 7}}}

    def test_a_duplicate_key_in_a_layer_still_fails_strictly(self, tmp_path):
        (tmp_path / "a.yaml").write_text("a: 1\nb: 2\na: 3\n")
        with pytest.raises(ValueError, match="'a'"):
            resolve_layers([tmp_path / "a.yaml"])


def test_a_file_s_deletions_survive_until_it_is_laid_over_the_files_below(tmp_path) -> None:
    # sim.yaml deletes rig.yaml's real links: its nulls must reach the layering, not be
    # dropped while the file is loaded on its own (the aging-room / mushroom-room overlays).
    rig = tmp_path / "rig.yaml"
    rig.write_text("links:\n  i2c1: {type: i2c}\n  plant: {type: sim_plant}\n")
    sim = tmp_path / "sim.yaml"
    sim.write_text("links:\n  i2c1: null\n")
    document, _ = resolve_layers([rig, sim])
    assert document["links"] == {"plant": {"type": "sim_plant"}}
    base = tmp_path / "base.yaml"
    base.write_text("name: lab\n")
    child = tmp_path / "child.yaml"
    child.write_text("extends: [base.yaml]\nlinks:\n  i2c1: null\n")
    document, _ = resolve_layers([rig, child])
    assert document["links"] == {"plant": {"type": "sim_plant"}}, "through an extends too"


def test_a_deletion_with_nothing_beneath_leaves_nothing_behind(tmp_path) -> None:
    # A saved overlay can delete a device an earlier save added, over files that never had it.
    rig = tmp_path / "lab.yaml"
    rig.write_text("name: lab\n")
    added = tmp_path / "added.yaml"
    added.write_text("devices:\n  probe: null\n")
    document, _ = resolve_layers([rig, added])
    assert document == {"name": "lab", "devices": {}}


FIXTURES = Path(__file__).parent / "fixtures" / "layers"


class TestSharedFixtures:
    """The cases `fixtures/layers` shares with the Go implementation (`rigfile/overlay_test.go`)."""

    @pytest.mark.parametrize("case", sorted(p.name for p in FIXTURES.iterdir() if p.is_dir()))
    def test_the_merged_document_is_the_expected_one(self, case):
        spec = json.loads((FIXTURES / case / "expected.json").read_text())
        document, _ = resolve_layers([FIXTURES / case / layer for layer in spec["layers"]])
        assert document == spec["document"]


class TestEachFileOnce:
    def test_a_file_two_bases_share_is_merged_once_under_both(self, tmp_path):
        (tmp_path / "project.yaml").write_text("links: {bus: {type: sim_plant, rate: 1}}\n")
        (tmp_path / "variant.yaml").write_text("extends: [project.yaml]\nlinks: {bus: {rate: 3}}\n")
        (tmp_path / "machine.yaml").write_text("extends: [variant.yaml, project.yaml]\n")
        document, files = resolve_layers([tmp_path / "machine.yaml"])
        assert document["links"]["bus"]["rate"] == 3, "the project came back over the variant"
        assert files == [tmp_path / f"{n}.yaml" for n in ("project", "variant", "machine")]


class TestNameSpellings:
    def test_both_spellings_of_a_name_are_one_entry_across_layers(self, tmp_path):
        (tmp_path / "a.yaml").write_text("devices: {dry-air: {driver: values}}\n")
        (tmp_path / "b.yaml").write_text("devices: {dry_air: {poll_s: 2}}\n")
        document, _ = resolve_layers([tmp_path / "a.yaml", tmp_path / "b.yaml"])
        assert document["devices"] == {"dry_air": {"driver": "values", "poll_s": 2}}

    def test_both_spellings_in_one_file_are_refused(self, tmp_path):
        (tmp_path / "a.yaml").write_text(
            "links: {bus-1: {type: sim_plant}, bus_1: {type: sim_plant}}\n"
        )
        with pytest.raises(ValueError, match="one name, given twice"):
            resolve_layers([tmp_path / "a.yaml"])

    def test_a_set_reaches_the_entry_in_either_spelling(self, tmp_path):
        (tmp_path / "a.yaml").write_text("devices: {dry_air: {driver: values, poll_s: 1}}\n")
        document, _ = resolve_layers([tmp_path / "a.yaml"], ["devices.dry-air.poll_s=5"])
        assert document["devices"] == {"dry_air": {"driver": "values", "poll_s": 5}}
