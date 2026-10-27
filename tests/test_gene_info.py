"""Tests for the per-gene dialog's markdown builder.

The dialog needs a browser; these are the parts with logic. What they guard is
mostly formatting with a wrong answer available: a UCSC link needs a ``chr``
prefix the data does not store, the track hub belongs to one assembly only, and
a link whose identifier the gene lacks must disappear rather than 404.
"""

from __future__ import annotations

from typing import ClassVar

import polars as pl
import pytest

from genetrek.schema import LocusColumns, Roles
from genetrek.ui.components.gene_info_dialog import (
    ensembl_url,
    format_locus,
    gene_links_markdown,
    identifier_rows,
    plain_locus,
    resolve_external_links,
    resolve_gene,
    ucsc_url,
)

GRCH38 = LocusColumns(assembly="GRCh38", chrom="c38", start="s38", end="e38")
GRCH37 = LocusColumns(assembly="GRCh37", chrom="c37", start="s37", end="e37")


@pytest.fixture
def roles() -> Roles:
    """Roles shaped like the real database, but with neutral column names."""
    return Roles(
        primary_key="key",
        symbol="sym",
        ensg="gene_id",
        ncbi="entrez",
        hgnc="key",
        omim="mim",
        assemblies={"GRCh38": GRCH38, "GRCh37": GRCH37},
    )


@pytest.fixture
def row() -> dict[str, object]:
    """SHANK3's real values, under this test's column names."""
    return {
        "key": "HGNC:14294",
        "sym": "SHANK3",
        "gene_id": "ENSG00000251322",
        "entrez": "85358",
        "mim": "606230",
        "hgnc_location": "22q13.33",
        "c38": "22",
        "s38": 50674408,
        "e38": 50733212,
        "c37": "22",
        "s37": 51112836,
        "e37": 51171640,
        "strand": "+",
    }


class FakeConfig:
    """Only what the functions under test read off an AppConfig."""

    def __init__(
        self,
        roles: Roles,
        links: dict[str, object] | None = None,
        row: dict[str, object] | None = None,
    ) -> None:
        self.roles = roles
        self.external_links_config = links if links is not None else {"links": []}
        # Labels come from the metadata, so the dialog shows a database's own
        # wording rather than wording hardcoded in the view.
        self.display_config = {
            "entrez": {"fullname": "NCBI ID"},
            "gene_id": {"fullname": "Ensembl ID"},
            "key": {"fullname": "HGNC ID"},
            "mim": {"fullname": "OMIM ID"},
            "hgnc_location": {"fullname": "Cytogenetic location"},
        }
        self.df = pl.DataFrame([row]) if row else pl.DataFrame({"key": [], "sym": []})


class TestFormatLocus:
    def test_it_reads_thousands_separated_with_a_span(self, row):
        assert format_locus(row, GRCH38) == "22:50,674,408-50,733,212 (58,805 bp)"

    def test_an_assembly_the_gene_lacks_is_none_rather_than_a_partial_string(self, row):
        assert (
            format_locus({**row, "c37": None, "s37": None, "e37": None}, GRCH37) is None
        )

    def test_a_chromosome_without_bounds_is_not_rendered(self, row):
        assert format_locus({**row, "s38": None}, GRCH38) is None

    def test_the_span_is_inclusive(self):
        locus = LocusColumns(assembly="X", chrom="c", start="s", end="e")
        assert "(1 bp)" in format_locus({"c": "1", "s": 10, "e": 10}, locus)


class TestUcscUrl:
    def test_it_uses_ucscs_own_name_for_the_assembly(self, row, roles):
        assert "db=hg38" in ucsc_url("GRCh38", row, roles)
        assert "db=hg19" in ucsc_url("GRCh37", row, roles)

    def test_the_track_hub_rides_on_the_assembly_that_declares_it(self, row, roles):
        """The hub file says `genome hg38`, so it must not be sent to hg19."""
        assert "hubUrl=" in ucsc_url("GRCh38", row, roles)
        assert "hubUrl=" not in ucsc_url("GRCh37", row, roles)

    def test_an_unknown_assembly_yields_no_link_rather_than_a_guess(self, row, roles):
        assert ucsc_url("T2T-CHM13", row, roles) is None

    def test_a_gene_with_no_symbol_yields_no_link(self, row, roles):
        assert ucsc_url("GRCh38", {**row, "sym": None}, roles) is None

    def test_a_symbol_needing_escaping_does_not_truncate_the_query(self, row, roles):
        """No current symbol needs it; one with an '&' would break the URL."""
        url = ucsc_url("GRCh38", {**row, "sym": "A&B#C"}, roles)
        assert url.endswith("position=A%26B%23C")

    def test_the_hub_url_keeps_its_own_scheme_readable(self, row, roles):
        assert "hubUrl=https://genetrek.pasteur.fr/" in ucsc_url("GRCh38", row, roles)


class TestEnsemblUrl:
    def test_the_older_assembly_comes_from_the_archive_host(self, row, roles):
        assert ensembl_url("GRCh38", row, roles).startswith("https://www.ensembl.org/")
        assert ensembl_url("GRCh37", row, roles).startswith(
            "https://grch37.ensembl.org/"
        )

    def test_it_links_by_gene_id_not_by_symbol(self, row, roles):
        assert ensembl_url("GRCh38", row, roles).endswith("g=ENSG00000251322")

    def test_a_gene_with_no_ensembl_id_yields_no_link(self, row, roles):
        assert ensembl_url("GRCh38", {**row, "gene_id": None}, roles) is None

    def test_an_unknown_assembly_yields_no_link(self, row, roles):
        assert ensembl_url("T2T-CHM13", row, roles) is None


class TestResolveExternalLinks:
    LINKS: ClassVar[dict[str, object]] = {
        "links": [
            {"name": "NCBI Gene", "url": "https://ncbi/{entrez}"},
            {"name": "OMIM", "url": "https://omim/{mim}"},
        ],
    }

    def test_placeholders_are_filled_from_the_row(self):
        resolved = resolve_external_links(
            self.LINKS, {"entrez": "85358", "mim": "606230"}
        )
        assert resolved == [
            ("NCBI Gene", "https://ncbi/85358"),
            ("OMIM", "https://omim/606230"),
        ]

    @pytest.mark.parametrize("missing", [None, "", "   "])
    def test_a_link_the_gene_cannot_fill_is_dropped_not_half_substituted(self, missing):
        """Better no OMIM link than one pointing at `/{mim}`."""
        resolved = resolve_external_links(
            self.LINKS, {"entrez": "85358", "mim": missing}
        )
        assert [name for name, _ in resolved] == ["NCBI Gene"]

    def test_configured_order_is_preserved(self):
        resolved = resolve_external_links(self.LINKS, {"entrez": "1", "mim": "2"})
        assert [name for name, _ in resolved] == ["NCBI Gene", "OMIM"]

    def test_a_malformed_entry_is_skipped_rather_than_raising(self):
        assert resolve_external_links({"links": ["not a mapping", None]}, {}) == []

    def test_no_links_configured_is_not_an_error(self):
        assert resolve_external_links({}, {"entrez": "1"}) == []


class TestGeneLinksMarkdown:
    def test_the_current_assembly_is_listed_first(self, row, roles):
        body = gene_links_markdown(FakeConfig(roles), row)
        assert body.index("GRCh38") < body.index("GRCh37")

    def test_the_duplicate_ensembl_entry_is_not_offered_twice(self, row, roles):
        """The generated external_links.yaml carries its own Ensembl link."""
        links = {
            "links": [
                {
                    "name": "Ensembl",
                    "url": "https://www.ensembl.org/Homo_sapiens/Gene/Summary?g={gene_id}",
                },
                {"name": "GeneCards", "url": "https://genecards/{sym}"},
            ],
        }
        body = gene_links_markdown(FakeConfig(roles, links), row)
        summary = "https://www.ensembl.org/Homo_sapiens/Gene/Summary?g=ENSG00000251322"
        assert body.count(summary) == 1
        assert "GeneCards" in body

    def test_it_no_longer_renders_the_coordinates(self):
        """Those are elements now, because each line carries a copy button."""
        blind = Roles(primary_key="key", symbol="sym", assemblies={"GRCh38": GRCH38})
        body = gene_links_markdown(FakeConfig(blind), {"c38": "22", "s38": 1, "e38": 9})
        assert "Coordinates" not in body

    def test_a_database_with_no_assemblies_still_renders_the_links(self, row):
        blind = Roles(primary_key="key", symbol="sym", ensg="gene_id")
        links = {"links": [{"name": "GeneCards", "url": "https://genecards/{sym}"}]}
        body = gene_links_markdown(FakeConfig(blind, links), row)
        assert "GeneCards" in body


class TestPlainLocus:
    def test_it_is_prefixed_and_free_of_separators(self, row):
        assert plain_locus(row, GRCH38) == "chr22:50674408-50733212"

    def test_it_never_carries_a_thousands_separator(self, row):
        """The regression this guards.

        A separated locus pasted into the search box is split on its commas,
        and the fragments are then read as NCBI gene IDs -- the real database
        answered `22:50,674,408-50,733,212` with ALAS2 and C8G.
        """
        assert "," not in plain_locus(row, GRCH38)

    def test_a_database_already_storing_the_prefix_is_not_doubled(self, row):
        assert plain_locus({**row, "c38": "chr22"}, GRCH38) == "chr22:50674408-50733212"

    def test_an_assembly_the_gene_lacks_has_nothing_to_copy(self, row):
        assert plain_locus({**row, "c37": None}, GRCH37) is None

    def test_the_mitochondrion_keeps_the_spelling_browsers_expect(self, row):
        assert plain_locus({**row, "c38": "M"}, GRCH38).startswith("chrM:")


class TestIdentifierRows:
    def test_the_identifiers_are_labelled_from_the_metadata(self, row, roles):
        assert identifier_rows(FakeConfig(roles), row) == [
            ("NCBI ID", "85358"),
            ("Ensembl ID", "ENSG00000251322"),
            ("HGNC ID", "HGNC:14294"),
            ("OMIM ID", "606230"),
            ("Cytogenetic location", "22q13.33"),
        ]

    def test_an_identifier_the_gene_lacks_is_omitted_not_left_blank(self, row, roles):
        labels = [
            label
            for label, _ in identifier_rows(FakeConfig(roles), {**row, "mim": None})
        ]
        assert "OMIM ID" not in labels
        assert "NCBI ID" in labels

    def test_a_database_without_the_band_still_shows_the_rest(self, row, roles):
        without = {k: v for k, v in row.items() if k != "hgnc_location"}
        labels = [label for label, _ in identifier_rows(FakeConfig(roles), without)]
        assert "Cytogenetic location" not in labels
        assert len(labels) == 4

    def test_a_role_no_database_fills_contributes_nothing(self, row):
        """An unfilled role must not raise, and must not invent a row.

        The band survives regardless: it has no role at all, so it is read by
        name and does not depend on the roles being resolved.
        """
        partial = Roles(primary_key="key", symbol="sym", ncbi="entrez")
        assert identifier_rows(FakeConfig(partial), row) == [
            ("NCBI ID", "85358"),
            ("Cytogenetic location", "22q13.33"),
        ]


class TestResolveGene:
    def test_a_primary_key_resolves_to_itself(self, row, roles):
        assert resolve_gene(FakeConfig(roles, row=row), "HGNC:14294") == "HGNC:14294"

    def test_a_symbol_resolves_to_the_primary_key(self, row, roles):
        assert resolve_gene(FakeConfig(roles, row=row), "SHANK3") == "HGNC:14294"

    @pytest.mark.parametrize("typed", ["shank3", "Shank3", "  SHANK3  "])
    def test_a_symbol_from_a_url_is_matched_however_it_was_typed(
        self,
        row,
        roles,
        typed,
    ):
        assert resolve_gene(FakeConfig(roles, row=row), typed) == "HGNC:14294"

    @pytest.mark.parametrize("unknown", ["NOPE", "", "   "])
    def test_an_unknown_value_resolves_to_nothing(self, row, roles, unknown):
        assert resolve_gene(FakeConfig(roles, row=row), unknown) is None

    def test_a_database_with_no_primary_key_resolves_nothing(self, row):
        keyless = Roles(symbol="sym")
        assert resolve_gene(FakeConfig(keyless, row=row), "SHANK3") is None
