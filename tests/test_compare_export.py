"""Supplementary tables and figure sizing for a gene-set comparison.

These files are meant to be attached to a paper as they are, so the tests read
them back: the membership table must reproduce every set, the workbook must
hold every sheet, and a figure exported at 89 mm must measure 89 mm.
"""

from __future__ import annotations

import io
import json
import math
import zipfile
from datetime import date

import polars as pl
import pytest

from genetrek.services import compare as cmp
from genetrek.services import compare_export as export
from genetrek.services import compare_figures as figures

UNIVERSE = frozenset(f"HGNC:{i}" for i in range(1, 201))
FRAME = pl.DataFrame(
    {
        "hgnc_id": [f"HGNC:{i}" for i in range(1, 201)],
        "sym": [f"G{i:03d}" for i in range(1, 201)],
    },
)


def gene_set(label: str, ids, **details: str) -> cmp.GeneSet:
    return cmp.GeneSet(
        id=label,
        label=label,
        name=f"The {label} list",
        kind="database",
        genes=frozenset(f"HGNC:{i}" for i in ids),
        column=label.lower(),
        details=details,
    )


@pytest.fixture
def comparison() -> cmp.Comparison:
    sets = [
        gene_set("NDD", range(1, 60), update="20260918", doi="10.1/x"),
        gene_set("SYN", range(40, 120)),
        gene_set("ASD", [*range(50, 70), 500]),  # 500 is outside the universe
    ]
    return cmp.compare(
        sets,
        UNIVERSE,
        background="Protein-coding genes",
        construction={(0, 2): "ASD is derived from NDD"},
    )


class TestGenesets:
    def test_columns_reproduce_every_set(self, comparison):
        table = export.genesets_table(comparison, FRAME, "hgnc_id", "sym")
        for s in comparison.sets:
            members = set(table.filter(pl.col(s.label) == 1)["hgnc_id"])
            assert members == s.genes

    def test_regions_partition_the_union(self, comparison):
        table = export.genesets_table(comparison, FRAME, "hgnc_id", "sym")
        assert table.height == len(comparison.union)
        assert table["hgnc_id"].n_unique() == table.height
        assert (
            table["n_sets"] == table.select(["NDD", "SYN", "ASD"]).sum_horizontal()
        ).all()

    def test_symbols_and_order(self, comparison):
        table = export.genesets_table(comparison, FRAME, "hgnc_id", "sym")
        assert table.columns == [
            "hgnc_id",
            "sym",
            "NDD",
            "SYN",
            "ASD",
            "n_sets",
            "region",
        ]
        largest = comparison.regions[0]
        first = table.row(0, named=True)
        assert first["region"] == " & ".join(
            comparison.sets[i].label for i in largest.members
        )
        assert first["sym"] is not None

    def test_a_label_clashing_with_a_column_is_renamed(self):
        sets = [gene_set("sym", range(1, 5)), gene_set("B", range(3, 9))]
        comparison = cmp.compare(sets, UNIVERSE, background="x")
        table = export.genesets_table(comparison, FRAME, "hgnc_id", "sym")
        assert "sym (2)" in table.columns


class TestSetsAndStatistics:
    def test_sets_carry_provenance_and_background_loss(self, comparison):
        table = export.sets_table(comparison, "20260918")
        asd = table.filter(pl.col("label") == "ASD").row(0, named=True)
        assert (asd["genes_total"], asd["genes_in_background"]) == (21, 20)
        ndd = table.filter(pl.col("label") == "NDD").row(0, named=True)
        assert (ndd["version_date"], ndd["doi"]) == ("20260918", "10.1/x")

    def test_statistics_one_row_per_pair(self, comparison):
        table = export.statistics_table(comparison)
        assert table.height == 3
        flagged = table.filter(pl.col("overlap_by_construction") != "")
        assert flagged.select("set_a", "set_b").row(0) == ("NDD", "ASD")

    def test_infinite_and_underflowed_values_are_honest_text(self):
        inner = gene_set("IN", range(1, 11))
        outer = gene_set("OUT", range(1, 101))
        comparison = cmp.compare([inner, outer], UNIVERSE, background="x")
        row = export.statistics_table(comparison).row(0, named=True)
        assert row["odds_ratio"] == "Inf"
        # The exact interval still bounds an infinite estimate from below.
        assert float(row["or_ci95_lower"]) > 1
        assert row["or_ci95_upper"] == "Inf"

    def test_a_zero_p_value_is_written_as_a_bound(self):
        assert export._p(0.0) == "<1e-300"
        assert export._p(0.0123) == "0.0123"


class TestMethods:
    def test_names_background_n_and_k(self, comparison):
        text = export.methods_text(
            comparison,
            release="20260918",
            background_definition="genes whose locus is coding",
            today=date(2026, 10, 6),
        )
        assert "20260918" in text
        assert "2026-10-06" in text
        assert "200 genes" in text
        assert "Protein-coding genes" in text
        assert "each of the 3 pairs" in text
        assert "Benjamini-Hochberg" in text
        assert "ASD is derived from NDD" in text


class TestBundles:
    def tables(self, comparison):
        return {
            "genesets": export.genesets_table(comparison, FRAME, "hgnc_id", "sym"),
            "sets": export.sets_table(comparison, "r"),
            "statistics": export.statistics_table(comparison),
        }

    def test_xlsx_has_every_sheet(self, comparison):
        data = export.xlsx_bytes(self.tables(comparison), "Methods.\n\nMore.")
        with zipfile.ZipFile(io.BytesIO(data)) as workbook:
            names = workbook.read("xl/workbook.xml").decode()
        for sheet in ("genesets", "sets", "statistics", "methods"):
            assert f'name="{sheet}"' in names

    def test_zip_has_tsvs_and_readme(self, comparison):
        data = export.zip_bytes(self.tables(comparison), "Methods text")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert sorted(archive.namelist()) == [
                "README.txt",
                "genesets.tsv",
                "sets.tsv",
                "statistics.tsv",
            ]
            back = pl.read_csv(archive.read("genesets.tsv"), separator="\t")
        assert back.height == len(comparison.union)


class TestFigureSizing:
    @pytest.mark.parametrize(("mm", "dpi", "px"), [(89, 300, 1051), (183, 300, 2161)])
    def test_png_width_at_publication_sizes(self, mm, dpi, px):
        assert round(figures.mm_to_px(mm) * figures.png_scale(dpi)) == px

    def test_export_style_is_in_points(self):
        style = figures.export_style(89)
        assert style.font_size == pytest.approx(7 * 96 / 72)
        assert style.line_width == pytest.approx(0.5 * 96 / 72)
        assert style.width == pytest.approx(89 / 25.4 * 96)

    @pytest.mark.parametrize("build", ["venn", "upset", "heatmap"])
    def test_export_figures_take_the_requested_width(self, comparison, build):
        style = figures.export_style(120)
        figure = {
            "venn": figures.venn_figure,
            "upset": figures.upset_figure,
            "heatmap": figures.heatmap_figure,
        }[build](comparison, style=style)
        assert figure.layout.width == pytest.approx(figures.mm_to_px(120))
        assert figure.layout.height > 0
        assert figure.layout.font.family.startswith("Arial")

    def test_screen_venn_regions_are_clickable(self, comparison):
        figure = figures.venn_figure(comparison)
        clicks = [t for t in figure.data if t.customdata is not None]
        assert len(clicks) == 1
        members = {tuple(int(i) for i in c.split(",")) for c in clicks[0].customdata}
        assert {r.members for r in comparison.regions} <= members

    def test_export_venn_has_no_hover_layer(self, comparison):
        figure = figures.venn_figure(comparison, style=figures.export_style(89))
        assert all(t.customdata is None for t in figure.data)

    def test_heatmap_marks_constructed_pairs(self, comparison):
        figure = figures.heatmap_figure(comparison, "jaccard")
        text = figure.data[0].text
        assert text[0][2].endswith("†")
        assert not text[0][1].endswith("†")
        assert math.isclose(
            figure.data[0].z[0][1],
            comparison.pairs[0].jaccard,
        )


def test_an_underflowed_fdr_is_shown_as_a_bound():
    inner = cmp.GeneSet(
        "a", "A", "A", "database", frozenset(str(i) for i in range(400))
    )
    outer = cmp.GeneSet(
        "b", "B", "B", "database", frozenset(str(i) for i in range(410))
    )
    comparison = cmp.compare(
        [inner, outer, cmp.GeneSet("c", "C", "C", "database", frozenset({"1"}))],
        frozenset(str(i) for i in range(20000)),
        background="x",
    )
    assert comparison.pairs[0].fdr == 0
    figure = figures.heatmap_figure(comparison, "signed_neg_log10_fdr")
    assert figure.data[0].text[0][1] == ">300"


def test_every_heatmap_metric_is_explained():
    assert set(figures.HEATMAP_EXPLANATIONS) == set(figures.HEATMAP_METRICS)


def _stratified(comparison_sets) -> cmp.Comparison:
    frame = pl.DataFrame(
        {
            "hgnc_id": [f"HGNC:{i}" for i in range(1, 201)],
            "score": [float(i % 50) for i in range(1, 201)],
        },
    )
    return cmp.compare(
        comparison_sets,
        UNIVERSE,
        background="Protein-coding genes",
        strata=cmp.strata_for(frame, "hgnc_id", "score", 5),
        covariate="score quintile",
    )


class TestAdjustedExports:
    SETS = (
        gene_set("NDD", range(1, 60)),
        gene_set("SYN", range(40, 120)),
        gene_set("ASD", range(150, 170)),
    )

    def test_statistics_carry_the_adjusted_columns(self):
        table = export.statistics_table(_stratified(list(self.SETS)))
        for column in (
            "adjusted_for",
            "mh_odds_ratio",
            "mh_ci95_lower",
            "mh_ci95_upper",
            "cmh_p_value",
            "cmh_fdr_bh",
            "strata_used",
        ):
            assert column in table.columns
        assert set(table["adjusted_for"]) == {"score quintile"}

    def test_methods_name_the_stratified_analysis_and_the_api(self):
        text = export.methods_text(
            _stratified(list(self.SETS)),
            release="r",
            background_definition="d",
            today=date(2026, 10, 6),
            covariate_definition="5 quantile bins of score",
        )
        assert "conditional maximum-likelihood odds ratio" in text
        assert "Cochran-Mantel-Haenszel" in text
        assert "score quintile (5 quantile bins of score)" in text
        assert "POST /api/compare" in text

    def test_request_body_names_fields_and_inlines_the_rest(self):
        mine = cmp.GeneSet(
            "list:x", "Mine", "Mine", "my_list", frozenset({"HGNC:7", "HGNC:999"})
        )
        comparison = cmp.compare(
            [self.SETS[0], mine],
            frozenset({"HGNC:7", "HGNC:8"}),
            background="Protein-coding genes",
        )
        body = export.request_body(comparison)
        assert body["sets"] == [
            {"field": "ndd", "label": "NDD"},
            # Before the background restriction: HGNC:999 is kept.
            {"label": "Mine", "genes": ["HGNC:7", "HGNC:999"]},
        ]
        assert body["background"] == "Protein-coding genes"
        assert body["adjust_for"] == []

    def test_bundles_carry_alt_text_and_the_request(self):
        comparison = _stratified(list(self.SETS))
        tables = {"statistics": export.statistics_table(comparison)}
        alt = {"Venn diagram": figures.alt_text(comparison, "venn")}
        request = export.request_body(comparison)
        data = export.zip_bytes(tables, "M", alt_texts=alt, request=request)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert "figures.txt" in archive.namelist()
            assert json.loads(archive.read("request.json")) == request
        data = export.xlsx_bytes(tables, "M", alt_texts=alt, request=request)
        with zipfile.ZipFile(io.BytesIO(data)) as workbook:
            names = workbook.read("xl/workbook.xml").decode()
        assert 'name="figures"' in names
        assert 'name="request"' in names


class TestSignedHeatmap:
    def test_depletion_is_negative_enrichment_positive(self):
        universe = frozenset(str(i) for i in range(2000))
        a = cmp.GeneSet(
            "a", "A", "A", "database", frozenset(str(i) for i in range(200))
        )
        same = cmp.GeneSet(
            "b", "B", "B", "database", frozenset(str(i) for i in range(150))
        )
        apart = cmp.GeneSet(
            "c", "C", "C", "database", frozenset(str(i) for i in range(1000, 1600))
        )
        comparison = cmp.compare([a, same, apart], universe, background="x")
        z = figures.heatmap_figure(comparison, "signed_neg_log10_fdr").data[0].z
        assert z[0][1] > 1.3  # A and B share far more than chance
        assert z[0][2] < -1.3  # A and C share none of an expected 60

    def test_adjusted_heatmap_reads_the_stratified_fdr(self):
        comparison = _stratified(list(TestAdjustedExports.SETS))
        crude = figures.heatmap_figure(comparison, "signed_neg_log10_fdr").data[0].z
        adjusted = (
            figures.heatmap_figure(comparison, "signed_neg_log10_fdr", adjusted=True)
            .data[0]
            .z
        )
        assert crude != adjusted


class TestAltText:
    def test_names_every_set_and_count(self, comparison):
        text = figures.alt_text(comparison, "venn")
        for s in comparison.sets:
            assert f"{s.label} ({len(s.genes):,} genes)" in text
        for region in comparison.regions:
            assert f"{comparison.region_label(region.members)}: {region.size:,}" in text
        assert "Protein-coding genes" in text

    def test_heatmap_lists_every_pair(self, comparison):
        text = figures.alt_text(comparison, "heatmap", "jaccard")
        assert text.count(" and ") >= len(comparison.pairs)
        assert "overlap by construction" in text

    def test_upset_says_what_it_shows(self, comparison):
        assert figures.alt_text(comparison, "upset").startswith(
            "UpSet plot of 3 gene sets"
        )
