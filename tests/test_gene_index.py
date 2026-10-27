"""Tests for identifier normalisation, detection, and resolution."""

from __future__ import annotations

import dataclasses

import polars as pl
import pytest

from genetrek import schema
from genetrek.db import Database
from genetrek.db.gene_index import (
    GeneIndex,
    GeneIndexError,
    classify_id,
    detect_id_type,
    normalize,
)


@pytest.fixture
def index(resources):
    return GeneIndex.from_frame(Database.open(resources.root).frame)


@pytest.fixture
def rich_index(resources, hgnc_file):
    return GeneIndex.from_frame(Database.open(resources.root).frame).with_hgnc(
        hgnc_file
    )


class TestClassify:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("HGNC:5", "hgnc"),
            ("hgnc:37133", "hgnc"),
            ("ENSG00000121410", "ensg"),
            ("ENSG00000121410.12", "ensg"),
            ("ensg00000121410", "ensg"),
            ("1", "ncbi"),
            ("85358", "ncbi"),
            ("SHANK3", "symbol"),
            ("A1BG-AS1", "symbol"),
            ("  SHANK3  ", "symbol"),
            ("1.0", "symbol"),
        ],
    )
    def test_classify(self, value, expected):
        assert classify_id(value) == expected


class TestDetect:
    def test_homogeneous_symbols(self):
        detection = detect_id_type(["SHANK3", "A1BG", "MECP2"])
        assert detection.id_type == "symbol"
        assert detection.confidence == 1.0
        assert detection.warning is None

    def test_homogeneous_ensg(self):
        assert detect_id_type(["ENSG00000121410", "ENSG00000148584"]).id_type == "ensg"

    def test_homogeneous_hgnc(self):
        assert detect_id_type(["HGNC:5", "HGNC:7"]).id_type == "hgnc"

    def test_bare_integers_warn_about_ambiguity(self):
        detection = detect_id_type(["1", "2", "3"])
        assert detection.id_type == "ncbi"
        assert detection.warning is not None
        assert "hgnc_id" in detection.warning

    def test_heterogeneous_is_mixed(self):
        detection = detect_id_type(["SHANK3", "ENSG00000121410", "HGNC:5", "42"])
        assert detection.id_type == "mixed"

    def test_a_single_stray_is_enough_to_be_mixed(self):
        """A majority rule would silently fail the stray, which is worse."""
        assert (
            detect_id_type(["A", "B", "C", "D", "ENSG00000121410"]).id_type == "mixed"
        )

    def test_empty_input(self):
        detection = detect_id_type([])
        assert detection.confidence == 0.0
        assert detection.warning == "no values to inspect"

    def test_blanks_are_ignored(self):
        assert detect_id_type(["", "   ", "HGNC:5"]).id_type == "hgnc"


class TestNormalize:
    def test_hgnc_prefix_is_stripped(self):
        assert normalize(["HGNC:5", "hgnc: 37133", "5"], "hgnc").to_list() == [
            5,
            37133,
            5,
        ]

    def test_spreadsheet_floats_survive(self):
        """HGNC's entrez_id arrives as "1.0" after a spreadsheet round-trip."""
        assert normalize(["1.0", "85358.0", " 2 "], "ncbi").to_list() == [1, 85358, 2]

    def test_other_integer_prefixes(self):
        assert normalize(["GeneID:7", "OMIM:138670"], "ncbi").to_list() == [7, 138670]

    def test_unparseable_integer_becomes_null(self):
        assert normalize(["junk", ""], "ncbi").to_list() == [None, None]

    def test_ensg_version_suffix_is_dropped(self):
        assert normalize(
            ["ENSG00000251322.12", "ensg00000121410"], "ensg"
        ).to_list() == [
            "ENSG00000251322",
            "ENSG00000121410",
        ]

    def test_symbols_are_upper_cased_and_trimmed(self):
        assert normalize([" shank3 ", "A1BG"], "symbol").to_list() == ["SHANK3", "A1BG"]

    def test_unsupported_type_raises(self):
        with pytest.raises(GeneIndexError, match="cannot normalize"):
            normalize(["x"], "locus")

    def test_accepts_a_series(self):
        assert normalize(pl.Series(["HGNC:5"]), "hgnc").to_list() == [5]


class TestConstruction:
    def test_requires_the_primary_key(self):
        with pytest.raises(GeneIndexError, match="missing required identifier"):
            GeneIndex(pl.DataFrame({"symbol": ["A"]}))

    def test_requires_symbol(self):
        with pytest.raises(GeneIndexError, match="missing required identifier"):
            GeneIndex(pl.DataFrame({"ncbi_id": [1]}))

    def test_available_id_types_reflect_the_frame(self, index):
        assert set(index.available_id_types) == {
            "ncbi",
            "ensg",
            "symbol",
            "hgnc",
            "omim",
        }

    def test_available_id_types_shrink_without_columns(self):
        index = GeneIndex(pl.DataFrame({"ncbi_id": [1], "symbol": ["A"]}))
        assert set(index.available_id_types) == {"ncbi", "symbol"}

    def test_repr_mentions_hgnc_state(self, index, hgnc_file):
        assert "hgnc=not loaded" in repr(index)
        assert hgnc_file.name in repr(index.with_hgnc(hgnc_file))


class TestResolveByExplicitType:
    def test_by_ncbi(self, index):
        report = index.resolve(["3", "7"], id_type="ncbi")
        assert report.frame["ncbi_id"].to_list() == [3, 7]
        assert report.frame["matched_by"].to_list() == ["ncbi_id", "ncbi_id"]

    def test_by_symbol(self, index):
        report = index.resolve(["GENE05"], id_type="symbol")
        assert report.frame["ncbi_id"].to_list() == [5]

    def test_by_symbol_is_case_insensitive(self, index):
        assert index.resolve(["gene05"], id_type="symbol").frame["ncbi_id"][0] == 5

    def test_by_ensg(self, index):
        report = index.resolve(["ENSG00000000005"], id_type="ensg")
        assert report.frame["ncbi_id"].to_list() == [5]

    def test_by_ensg_ignores_the_version(self, index):
        assert index.resolve(["ENSG00000000005.9"], id_type="ensg").n_resolved == 1

    def test_by_hgnc_with_prefix(self, index):
        """The file says HGNC:50, the parquet stores 50."""
        assert index.resolve(["HGNC:50"], id_type="hgnc").frame["ncbi_id"][0] == 5

    def test_by_omim(self, index):
        assert index.resolve(["600005"], id_type="omim").frame["ncbi_id"][0] == 5

    def test_unknown_type_raises(self, index):
        with pytest.raises(GeneIndexError, match="unknown id_type"):
            index.resolve(["x"], id_type="nonsense")

    def test_type_without_a_column_raises(self):
        index = GeneIndex(pl.DataFrame({"ncbi_id": [1], "symbol": ["A"]}))
        with pytest.raises(GeneIndexError, match="has no 'ensg' column"):
            index.resolve(["ENSG00000000001"], id_type="ensg")


class TestResolveOutcomes:
    def test_unmatched_is_reported_not_raised(self, index):
        report = index.resolve(["NOSUCHGENE"], id_type="symbol")
        assert report.frame["ncbi_id"].to_list() == [None]
        assert report.unmatched == ["NOSUCHGENE"]
        assert not report.ok

    def test_duplicate_symbol_is_ambiguous(self, index):
        """GENE01 is used by two rows, so it cannot resolve to one gene."""
        report = index.resolve(["GENE01"], id_type="symbol")
        assert report.frame["matched_by"].to_list() == ["ambiguous"]
        assert report.frame["ncbi_id"].to_list() == [None]
        assert report.ambiguous == ["GENE01"]
        assert "2 candidate genes" in report.frame["note"][0]

    def test_row_count_matches_input_including_repeats(self, index):
        report = index.resolve(["GENE05", "GENE05", "GENE06"], id_type="symbol")
        assert report.n_input == 3
        assert report.frame["ncbi_id"].to_list() == [5, 5, 6]

    def test_resolved_deduplicates_on_the_key(self, index):
        report = index.resolve(["GENE05", "gene05"], id_type="symbol")
        assert report.resolved().height == 1

    def test_counts_tally_the_categories(self, index):
        report = index.resolve(["GENE05", "NOSUCHGENE"], id_type="symbol")
        assert report.counts == {"symbol": 1, "unmatched": 1}

    def test_ok_when_everything_resolves(self, index):
        assert index.resolve(["GENE05", "GENE06"], id_type="symbol").ok

    def test_empty_input(self, index):
        report = index.resolve([], id_type="symbol")
        assert report.n_input == 0
        assert report.ok

    def test_describe_lists_the_failures(self, index):
        described = index.resolve(["GENE05", "NOPE"], id_type="symbol").describe()
        assert "1/2 resolved" in described
        assert "which unmatched: NOPE" in described


class TestResolveAuto:
    def test_auto_detects_a_homogeneous_list(self, index):
        report = index.resolve(["GENE05", "GENE06"])
        assert report.id_type == "symbol"
        assert report.detected

    def test_explicit_type_is_not_flagged_as_detected(self, index):
        assert not index.resolve(["GENE05"], id_type="symbol").detected

    def test_auto_falls_back_to_mixed(self, index):
        # Gene 4 rather than 6: the fixture leaves every third ENSG null.
        report = index.resolve(["GENE05", "ENSG00000000004", "HGNC:70", "8"])
        assert report.id_type == "mixed"
        assert report.frame["ncbi_id"].to_list() == [5, 4, 7, 8]
        assert report.frame["matched_by"].to_list() == [
            "symbol",
            "ensg",
            "hgnc_id",
            "ncbi_id",
        ]

    def test_mixed_preserves_input_order(self, index):
        values = ["8", "GENE05", "7", "GENE06", "ENSG00000000002"]
        report = index.resolve(values, id_type="mixed")
        assert report.frame["input"].to_list() == values
        assert report.frame["ncbi_id"].to_list() == [8, 5, 7, 6, 2]

    def test_mixed_warns_about_bare_integers(self, index):
        report = index.resolve(["GENE05", "8"], id_type="mixed")
        assert any("bare integers" in w for w in report.warnings)

    def test_mixed_with_unmatched_values(self, index):
        report = index.resolve(["NOPE", "ENSG00000000002"], id_type="mixed")
        assert report.frame["ncbi_id"].to_list() == [None, 2]


class TestHgncEnrichment:
    def test_warns_when_symbols_resolve_without_hgnc(self, index):
        report = index.resolve(["GENE05"], id_type="symbol")
        assert any("no HGNC complete set" in w for w in report.warnings)

    def test_no_such_warning_once_loaded(self, rich_index):
        report = rich_index.resolve(["GENE05"], id_type="symbol")
        assert not any("no HGNC complete set" in w for w in report.warnings)

    def test_previous_symbol_resolves(self, rich_index):
        """A paper citing a retired name must still find the gene."""
        report = rich_index.resolve(["KIAA0002"], id_type="symbol")
        assert report.frame["ncbi_id"].to_list() == [2]
        assert report.frame["matched_by"].to_list() == ["prev_symbol"]
        assert report.frame["note"][0] == "renamed -> GENE02"

    def test_alias_symbol_resolves(self, rich_index):
        report = rich_index.resolve(["OLDALIAS"], id_type="symbol")
        assert report.frame["ncbi_id"].to_list() == [1]
        assert report.frame["matched_by"].to_list() == ["alias_symbol"]

    def test_every_alias_in_a_multi_value_field_resolves(self, rich_index):
        report = rich_index.resolve(["SECONDALIAS"], id_type="symbol")
        assert report.frame["ncbi_id"].to_list() == [1]

    def test_aliases_are_case_insensitive(self, rich_index):
        assert rich_index.resolve(["oldalias"], id_type="symbol").n_resolved == 1

    def test_exact_matches_take_precedence_over_aliases(self, rich_index):
        report = rich_index.resolve(["GENE05"], id_type="symbol")
        assert report.frame["matched_by"].to_list() == ["symbol"]

    def test_alias_bridge_beats_an_ambiguous_exact_symbol(self, rich_index):
        """GENE01 is ambiguous by symbol, but its alias bridges via entrez_id."""
        assert rich_index.resolve(["GENE01"], id_type="symbol").ambiguous == ["GENE01"]
        assert (
            rich_index.resolve(["OLDALIAS"], id_type="symbol").frame["ncbi_id"][0] == 1
        )

    def test_unrelated_symbols_still_fail(self, rich_index):
        assert rich_index.resolve(["NOSUCHGENE"], id_type="symbol").unmatched == [
            "NOSUCHGENE",
        ]

    def test_missing_file_raises(self, index, tmp_path):
        with pytest.raises(OSError, match=r"No such file|cannot find"):
            index.with_hgnc(tmp_path / "absent.txt")

    def test_no_file_anywhere_raises(self, index, resources, monkeypatch):
        monkeypatch.setenv("GENETREK_RESOURCES", str(resources.root))
        with pytest.raises(GeneIndexError, match="no HGNC complete set found"):
            index.with_hgnc()

    def test_default_path_uses_the_resources_tree(
        self, index, resources, hgnc_file, monkeypatch
    ):
        monkeypatch.setenv("GENETREK_RESOURCES", str(resources.root))
        assert index.with_hgnc().hgnc_path == hgnc_file

    def test_wrong_columns_raise(self, index, resources):
        bogus = resources.root / "original_file" / "hgnc_complete_set_bad.txt"
        pl.DataFrame({"nope": ["x"]}).write_csv(bogus, separator="\t")
        with pytest.raises(GeneIndexError, match="missing HGNC column"):
            index.with_hgnc(bogus)

    def test_loading_hgnc_returns_the_same_index(self, index, hgnc_file):
        assert index.with_hgnc(hgnc_file) is index


class TestAliasIndex:
    """The precomputed alias index, which is a deployment's only alias source.

    ``resources/original_file/`` is gitignored and excluded from the container
    image, so without this file a deployed GeneTrek resolves current symbols
    only and a search for a withdrawn one silently finds nothing.
    """

    @pytest.fixture
    def written(self, tmp_path, rich_index):
        path = tmp_path / "gene_aliases.parquet"
        rich_index.write_alias_index(path)
        return path

    def test_it_reproduces_the_complete_set_exactly(self, written, index, rich_index):
        """Otherwise the deployment answers differently from a laptop."""
        loaded = index.with_alias_index(written)
        queries = ["GENE01", "GENE02", "OLDALIAS", "KIAA0002", "NOTAGENE"]
        assert loaded.resolve(queries).frame.equals(rich_index.resolve(queries).frame)

    def test_retired_and_alias_symbols_resolve(self, written, index):
        report = index.with_alias_index(written).resolve(["OLDALIAS", "KIAA0002"])
        assert report.frame["matched_by"].to_list() == [
            "alias_symbol",
            "prev_symbol",
        ]
        assert "renamed -> GENE02" in report.frame["note"].to_list()[1]

    def test_loading_it_returns_the_same_index(self, written, index):
        assert index.with_alias_index(written) is index

    def test_coverage_distinguishes_the_three_states(
        self, written, index, rich_index, resources
    ):
        fresh = GeneIndex.from_frame(resources.frame)
        assert fresh.symbol_coverage == "exact-only"
        assert index.with_alias_index(written).symbol_coverage == "alias-index"
        assert rich_index.symbol_coverage == "hgnc"

    def test_it_silences_the_retired_symbol_warning(self, written, index):
        assert any("retired" in w for w in index.resolve(["GENE01"]).warnings)
        loaded = index.with_alias_index(written)
        assert not [w for w in loaded.resolve(["GENE01"]).warnings if "retired" in w]

    def test_the_complete_set_still_wins_when_both_are_present(
        self, written, index, hgnc_file
    ):
        both = index.with_alias_index(written).with_hgnc(hgnc_file)
        assert both.symbol_coverage == "hgnc"

    def test_it_records_the_provenance_a_staleness_check_needs(self, written, index):
        meta = index.with_alias_index(written).alias_index_meta
        assert meta["key_column"] == "ncbi_id"
        assert meta["frame_rows"] == "20"
        assert meta["source_file"].startswith("hgnc_complete_set")

    def test_writing_without_an_alias_source_is_refused(self, tmp_path, index):
        """A file holding only the parquet's own symbols looks like coverage."""
        with pytest.raises(GeneIndexError, match="no aliases to write"):
            index.write_alias_index(tmp_path / "empty.parquet")

    def test_a_missing_file_is_an_error_the_caller_can_catch(self, tmp_path, index):
        with pytest.raises(GeneIndexError, match="no alias index at"):
            index.with_alias_index(tmp_path / "absent.parquet")

    def test_an_index_built_against_another_key_is_refused(self, written, resources):
        """Silently resolving everything to nothing is the failure to avoid."""
        roles = dataclasses.replace(
            schema.resolve_roles(schema.load_fields_yaml(resources.fields_yaml)),
            primary_key="hgnc_id",
        )
        other = GeneIndex.from_frame(resources.frame, roles)
        with pytest.raises(GeneIndexError, match=r"different primary key"):
            other.with_alias_index(written)
