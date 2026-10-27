"""Tests for role resolution — finding a column by meaning, not by name."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import polars as pl
import pytest

from genetrek import schema
from genetrek.config.loader import load_config
from genetrek.db import Database, GeneIndex, GeneIndexError
from genetrek.schema import FieldMeta, LocusColumns, Roles, resolve_roles
from genetrek.services.enrichment import compute_enrichment
from genetrek.services.search import SearchUnavailableError, filter_dataframe


def meta(**kw: object) -> FieldMeta:
    return FieldMeta(fullname="x", **kw)


class TestResolveIdentifiers:
    def test_empty_metadata_fills_nothing(self):
        roles = resolve_roles({})
        assert roles.primary_key is None
        assert roles.identifiers == {}
        assert any("no field is marked primary_key" in p for p in roles.problems)

    def test_each_id_type_becomes_a_role(self):
        roles = resolve_roles(
            {
                "hgnc_symbol": meta(id_type="symbol"),
                "ensg": meta(id_type="ensg"),
                "ncbi_id": meta(id_type="ncbi", primary_key=True),
            },
        )
        assert roles.symbol == "hgnc_symbol"
        assert roles.ensg == "ensg"
        assert roles.ncbi == "ncbi_id"
        assert roles.primary_key == "ncbi_id"
        assert roles.problems == []

    def test_a_column_can_be_both_an_identifier_and_the_key(self):
        """ncbi_id is the NCBI ID *and* the resolution target."""
        roles = resolve_roles({"ncbi_id": meta(id_type="ncbi", primary_key=True)})
        assert roles.ncbi == "ncbi_id"
        assert roles.primary_key == "ncbi_id"

    def test_the_key_need_not_be_a_cross_reference(self):
        roles = resolve_roles({"hgnc_id": meta(id_type="hgnc", primary_key=True)})
        assert roles.primary_key == "hgnc_id"
        assert roles.hgnc == "hgnc_id"

    def test_two_primary_keys_is_a_problem(self):
        roles = resolve_roles(
            {"a": meta(primary_key=True), "b": meta(primary_key=True)},
        )
        assert roles.primary_key == "a"
        assert any("several fields claim primary_key" in p for p in roles.problems)

    def test_two_columns_claiming_one_id_type_is_a_problem(self):
        roles = resolve_roles(
            {"s1": meta(id_type="symbol"), "s2": meta(id_type="symbol")},
        )
        assert roles.symbol == "s1"
        assert any("several fields claim id_type 'symbol'" in p for p in roles.problems)

    def test_identifiers_omits_unfilled_roles(self):
        roles = resolve_roles({"s": meta(id_type="symbol")})
        assert roles.identifiers == {"symbol": "s"}


class TestResolveAssemblies:
    def test_a_complete_assembly_resolves(self):
        roles = resolve_roles(
            {
                "grch38_chr": meta(id_type="locus_chr", assembly="GRCh38"),
                "grch38_start": meta(id_type="locus_start", assembly="GRCh38"),
                "grch38_end": meta(id_type="locus_end", assembly="GRCh38"),
            },
        )
        locus = roles.assemblies["GRCh38"]
        assert (locus.chrom, locus.start, locus.end) == (
            "grch38_chr",
            "grch38_start",
            "grch38_end",
        )
        assert locus.strand is None
        assert locus.columns == ("grch38_chr", "grch38_start", "grch38_end")

    def test_strand_is_optional_and_included_when_present(self):
        roles = resolve_roles(
            {
                "c": meta(id_type="locus_chr", assembly="A"),
                "s": meta(id_type="locus_start", assembly="A"),
                "e": meta(id_type="locus_end", assembly="A"),
                "t": meta(id_type="locus_strand", assembly="A"),
            },
        )
        assert roles.assemblies["A"].strand == "t"
        assert len(roles.assemblies["A"].columns) == 4

    def test_two_assemblies_are_kept_apart(self):
        roles = resolve_roles(
            {
                "a_chr": meta(id_type="locus_chr", assembly="GRCh38"),
                "a_start": meta(id_type="locus_start", assembly="GRCh38"),
                "a_end": meta(id_type="locus_end", assembly="GRCh38"),
                "b_chr": meta(id_type="locus_chr", assembly="GRCh37"),
                "b_start": meta(id_type="locus_start", assembly="GRCh37"),
                "b_end": meta(id_type="locus_end", assembly="GRCh37"),
            },
        )
        assert sorted(roles.assemblies) == ["GRCh37", "GRCh38"]
        assert roles.assemblies["GRCh37"].chrom == "b_chr"

    def test_an_incomplete_assembly_is_dropped_with_a_problem(self):
        roles = resolve_roles(
            {
                "c": meta(id_type="locus_chr", assembly="A"),
                "s": meta(id_type="locus_start", assembly="A"),
            },
        )
        assert roles.assemblies == {}
        assert any("incomplete (no end)" in p for p in roles.problems)

    def test_a_locus_role_without_an_assembly_is_a_problem(self):
        roles = resolve_roles({"c": meta(id_type="locus_chr")})
        assert roles.assemblies == {}
        assert any("needs an assembly" in p for p in roles.problems)

    def test_the_legacy_locus_marker_contributes_nothing(self):
        """It cannot say which of chr/start/end it is, nor which assembly."""
        roles = resolve_roles({"hg38_chr": meta(id_type="locus")})
        assert roles.assemblies == {}
        assert any("unspecific id_type 'locus'" in p for p in roles.problems)


class TestSearchable:
    @pytest.mark.parametrize("role", ["symbol", "ensg", "ncbi"])
    def test_one_identifier_is_enough(self, role):
        assert Roles(**{role: "c"}).searchable

    def test_an_assembly_alone_is_enough(self):
        locus = LocusColumns(assembly="A", chrom="c", start="s", end="e")
        assert Roles(assemblies={"A": locus}).searchable

    def test_nothing_usable_is_not_searchable(self):
        assert not Roles(primary_key="k", hgnc="h", omim="o").searchable


class TestSearchFollowsRoles:
    def test_search_uses_whatever_the_symbol_column_is_called(self, resources):
        db = Database.open(resources.root)
        db.rename_field("symbol", "hgnc_symbol")
        db.save()
        config = load_config(resources.root)
        assert config.roles.symbol == "hgnc_symbol"
        found = filter_dataframe(
            config.df,
            "GENE05",
            exact_match=True,
            selected_bool_cols=[],
            results_merge="union",
            annotations_merge="union",
            selected_genome="hg38",
            roles=config.roles,
        )
        assert found.height == 1

    def test_locus_search_uses_the_renamed_coordinate_columns(self, resources):
        db = Database.open(resources.root)
        for old, new in (
            ("hg38_chr", "grch38_chr"),
            ("hg38_start", "grch38_start"),
            ("hg38_end", "grch38_end"),
        ):
            db.rename_field(old, new)
        db.save()
        config = load_config(resources.root)
        assert config.roles.assemblies["hg38"].chrom == "grch38_chr"

    def test_a_blind_database_raises_instead_of_returning_everything(self, resources):
        """The worst possible failure: 20 rows looks exactly like no search."""
        db = Database.open(resources.root)
        blind = dataclasses.replace(
            db.roles,
            symbol=None,
            ensg=None,
            ncbi=None,
            assemblies={},
        )
        with pytest.raises(SearchUnavailableError, match="cannot be performed"):
            filter_dataframe(
                db.frame,
                "GENE05",
                exact_match=True,
                selected_bool_cols=[],
                results_merge="union",
                annotations_merge="union",
                selected_genome="hg38",
                roles=blind,
            )

    def test_a_blind_database_still_allows_an_empty_search(self, resources):
        """No search text means no search, which is not a failure."""
        db = Database.open(resources.root)
        blind = dataclasses.replace(
            db.roles,
            symbol=None,
            ensg=None,
            ncbi=None,
            assemblies={},
        )
        assert (
            filter_dataframe(
                db.frame,
                "",
                exact_match=True,
                selected_bool_cols=[],
                results_merge="union",
                annotations_merge="union",
                selected_genome="hg38",
                roles=blind,
            ).height
            == db.n_rows
        )


class TestNoIdentifierNameIsHardcoded:
    """The role layer is only worth having if nothing bypasses it.

    A hardcoded identifier name fails *quietly*: a plot renders "Missing
    columns", a search stops matching a token type. `stats_drawer.py` did exactly
    that with the literal "symbol" until a database built by `genetrek-db init`
    named its symbol column `hgnc_symbol` and both plots went blank with every
    score present. Nothing else catches this, so a source scan does.
    """

    #: Column names that must come from Roles, never from a literal. Coordinate
    #: columns are matched by prefix since their names vary by assembly.
    ROLE_NAMES = ("symbol", "ensg", "ncbi_id", "hgnc_id", "omim_id")
    ROLE_PREFIXES = ("hg19_", "hg38_", "grch37_", "grch38_")

    def _modules(self):
        root = Path(schema.__file__).parent
        return sorted(
            path
            for directory in ("ui", "services", "config")
            for path in (root / directory).rglob("*.py")
        )

    def test_the_view_layer_names_no_identifier_column(self):
        offenders: list[str] = []
        for path in self._modules():
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                offenders.extend(
                    f"{path.name}:{lineno}: {name!r}"
                    for name in self.ROLE_NAMES
                    if f'"{name}"' in line or f"'{name}'" in line
                )
                offenders.extend(
                    f"{path.name}:{lineno}: {prefix!r}"
                    for prefix in self.ROLE_PREFIXES
                    if f'"{prefix}' in line or f"'{prefix}" in line
                )
        assert not offenders, (
            "these read an identifier or coordinate column by literal name; use "
            "config.roles / db.roles instead:\n  " + "\n  ".join(offenders)
        )

    def test_the_scan_would_actually_catch_one(self):
        """Guard the guard: a scan that matches nothing proves nothing."""
        planted = '    plot_df = filtered_df.select(["symbol", "pli"])\n'
        assert any(f'"{n}"' in planted for n in self.ROLE_NAMES)


class TestOptionalCodeColumns:
    """An absent fallback must not be reported as a broken feature."""

    def test_every_optional_column_is_a_code_dependency(self):
        """Otherwise the exclusion silences nothing and hides a typo."""
        assert set(schema.CODE_DEPENDENT_COLUMNS) >= schema.OPTIONAL_CODE_COLUMNS

    def test_the_required_ones_are_still_reported(self, resources):
        database = Database.open(resources.root)
        note = next(
            n for n in database.validate().notes if "referenced by name in code" in n
        )
        # loeuf is in the fixture, so it must not appear; inheritance is not.
        assert "inheritance" in note
        assert "loeuf" not in note

    def test_every_fallback_is_itself_a_code_dependency(self):
        stand_ins = {
            s for group in schema.CODE_COLUMN_FALLBACKS.values() for s in group
        }
        assert stand_ins <= set(schema.CODE_DEPENDENT_COLUMNS)
        assert set(schema.CODE_COLUMN_FALLBACKS) <= set(schema.CODE_DEPENDENT_COLUMNS)

    def test_a_present_stand_in_silences_the_note(self, resources):
        """A database still keyed on the older column is not told it is broken."""
        database = Database.open(resources.root)
        newer, (stand_in, *_) = next(iter(schema.CODE_COLUMN_FALLBACKS.items()))
        assert newer not in database.fields

        note = " ".join(database.validate().notes)
        assert f"{newer} (" in note, "the fixture should lack both, so it is reported"

        # The note inspects field *names* only, so how the column comes into
        # being is irrelevant -- a derived one avoids identifier resolution.
        database.add_derived_field(stand_in, ["flag_a"], meta={"fullname": stand_in})
        quieter = " ".join(database.validate().notes)
        assert f"{newer} (" not in quieter, "the stand-in did not silence the note"

    def test_an_absent_optional_column_is_not_reported(self, resources):
        database = Database.open(resources.root)
        notes = " ".join(database.validate().notes)
        for column in schema.OPTIONAL_CODE_COLUMNS:
            assert column not in database.fields, f"fixture carries {column}"
            assert f"{column} (" not in notes, f"{column} was reported as absent"


class TestSearchNeverReturnsEverything:
    """An unrecognised query must yield nothing, never the whole table."""

    def _find(self, config, text):
        return filter_dataframe(
            config.df,
            text,
            exact_match=True,
            selected_bool_cols=[],
            results_merge="union",
            annotations_merge="union",
            selected_genome="hg38",
            roles=config.roles,
        )

    def test_an_unrecognised_token_matches_nothing(self, resources):
        config = load_config(resources.root)
        assert self._find(config, "!!!").height == 0

    def test_a_colon_token_that_is_not_a_locus_matches_nothing(self, resources):
        config = load_config(resources.root)
        assert self._find(config, "NOTAGENE:zz").height == 0

    def test_an_absent_identifier_column_does_not_open_the_floodgates(self, resources):
        """Searching an ENSG on a database without one is 0 hits, not all of them."""
        db = Database.open(resources.root)
        blind = dataclasses.replace(db.roles, ensg=None)
        found = filter_dataframe(
            db.frame,
            "ENSG00000000005",
            exact_match=True,
            selected_bool_cols=[],
            results_merge="union",
            annotations_merge="union",
            selected_genome="hg38",
            roles=blind,
        )
        assert found.height == 0

    def test_a_blank_query_still_returns_every_row(self, resources):
        config = load_config(resources.root)
        assert self._find(config, "   ").height == config.df.height


class TestSearchFindsAnHgncIdentifier:
    """HGNC's canonical form carries a colon, which the locus branch used to eat."""

    def _find(self, config, text):
        return filter_dataframe(
            config.df,
            text,
            exact_match=True,
            selected_bool_cols=[],
            results_merge="union",
            annotations_merge="union",
            selected_genome="hg38",
            roles=config.roles,
        )

    @pytest.mark.parametrize("token", ["HGNC:30", "hgnc:30"])
    def test_the_prefix_resolves_in_either_case(self, resources, token):
        """The fixture stores hgnc_id as a bare integer, as the old database does."""
        config = load_config(resources.root)
        found = self._find(config, token)
        assert found.height == 1
        assert found["ncbi_id"][0] == 3

    def test_a_bare_number_is_still_read_as_an_ncbi_id(self, resources):
        """Unprefixed, a number is ambiguous; the app has always chosen NCBI."""
        config = load_config(resources.root)
        found = self._find(config, "3")
        assert found["ncbi_id"].to_list() == [3]

    def test_a_prefixed_value_in_the_column_also_resolves(self, resources):
        """A database built by 'genetrek-db init' stores it as HGNC:30."""
        db = Database.open(resources.root)
        frame = db.frame.with_columns(
            ("HGNC:" + pl.col("hgnc_id").cast(pl.String)).alias("hgnc_id"),
        )
        found = filter_dataframe(
            frame,
            "HGNC:30",
            exact_match=True,
            selected_bool_cols=[],
            results_merge="union",
            annotations_merge="union",
            selected_genome="hg38",
            roles=db.roles,
        )
        assert found.height == 1

    def test_an_unknown_hgnc_id_matches_nothing(self, resources):
        config = load_config(resources.root)
        assert self._find(config, "HGNC:999999").height == 0


class TestEnrichmentUsesOneKey:
    def test_both_sides_share_a_key(self, resources):
        db = Database.open(resources.root)
        subset = db.frame.filter(pl.col("flag_b"))
        result = compute_enrichment(db.frame, subset, "flag_a", db.roles)
        assert result.a + result.b == subset.height

    def test_a_keyless_database_yields_zeros_rather_than_nonsense(self, resources):
        db = Database.open(resources.root)
        blind = dataclasses.replace(
            db.roles,
            primary_key=None,
            ensg=None,
            symbol=None,
        )
        result = compute_enrichment(
            db.frame,
            db.frame.filter(pl.col("flag_b")),
            "flag_a",
            blind,
        )
        assert (result.a, result.b, result.c) == (0, 0, 0)


class TestGeneIndexUsesRoles:
    def test_no_primary_key_is_refused(self, resources):
        frame = Database.open(resources.root).frame
        with pytest.raises(GeneIndexError, match="no field is marked primary_key"):
            GeneIndex(frame, Roles(symbol="symbol"))

    def test_the_report_names_the_key_column(self, resources):
        db = Database.open(resources.root)
        report = db.resolve(["GENE05"], "symbol")
        assert report.key == "ncbi_id"
        assert report.key in report.frame.columns

    def test_a_string_primary_key_is_not_cast_to_an_integer(self):
        """The new schema keys on hgnc_id, "HGNC:5" -- casting would destroy it."""
        frame = pl.DataFrame(
            {
                "hgnc_id": ["HGNC:5", "HGNC:7", "HGNC:9"],
                "sym": ["AAA", "BBB", "CCC"],
            },
        )
        roles = Roles(primary_key="hgnc_id", symbol="sym", hgnc="hgnc_id")
        report = GeneIndex(frame, roles).resolve(["BBB"], "symbol")
        assert report.key == "hgnc_id"
        assert report.frame["hgnc_id"].dtype == pl.String
        assert report.frame["hgnc_id"].to_list() == ["HGNC:7"]

    def test_an_unfilled_id_type_is_refused_clearly(self):
        frame = pl.DataFrame({"k": [1], "sym": ["A"]})
        roles = Roles(primary_key="k", symbol="sym")
        with pytest.raises(GeneIndexError, match="no field declares that id_type"):
            GeneIndex(frame, roles).resolve(["ENSG00000000001"], "ensg")

    def test_legacy_construction_still_works(self, resources):
        """Callers predating the role layer pass no roles at all."""
        frame = Database.open(resources.root).frame
        index = GeneIndex(frame)
        assert index.key == "ncbi_id"
        assert index.resolve(["GENE05"], "symbol").n_resolved == 1


class TestValidateReportsRoleProblems:
    def test_a_healthy_fixture_has_no_role_problems(self, resources):
        db = Database.open(resources.root)
        assert db.roles.problems == []
        assert db.validate().ok

    def test_an_ambiguous_role_is_an_error(self, resources):
        db = Database.open(resources.root)
        db._fields["score"].id_type = "symbol"
        db._roles = schema.resolve_roles(db._fields)
        report = db.validate()
        assert not report.ok
        assert any("several fields claim id_type 'symbol'" in e for e in report.errors)

    def test_an_incomplete_assembly_is_only_a_warning(self, resources):
        db = Database.open(resources.root)
        db._fields["hg38_end"].id_type = None
        db._roles = schema.resolve_roles(db._fields)
        report = db.validate()
        assert report.ok
        assert any("incomplete" in w for w in report.warnings)
