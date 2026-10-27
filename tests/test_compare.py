"""Gene-set comparison: backgrounds, pairwise statistics, regions, flags.

The numbers here end up in papers, so the 2x2 tables are checked against
hand-computed values and against scipy directly, and the drawer's older
enrichment path is pinned to give the same answer as the new shared test.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import ClassVar

import polars as pl
import pytest
from scipy import stats
from scipy.stats.contingency import odds_ratio

from genetrek.backgrounds import (
    EVERY_GENE,
    Background,
    load_backgrounds,
    load_covariates,
    parse_backgrounds,
    parse_covariates,
)
from genetrek.config.loader import load_config
from genetrek.db.database import Database
from genetrek.schema import FieldMeta, Roles
from genetrek.services import compare as cmp
from genetrek.services.enrichment import compute_enrichment, fisher_2x2

UNIVERSE = frozenset(str(i) for i in range(100))


def gene_set(label: str, genes, column: str | None = None) -> cmp.GeneSet:
    return cmp.GeneSet(
        id=f"db:{column}" if column else label,
        label=label,
        name=label,
        kind="database" if column else "my_list",
        genes=frozenset(str(g) for g in genes),
        column=column,
    )


A = gene_set("A", range(20))  # 20 genes
B = gene_set("B", range(10, 40))  # 30 genes, 10 shared with A
C = gene_set("C", range(35, 45))  # 10 genes, 5 shared with B, none with A


class TestFisher:
    """The conditional MLE odds ratio and exact CI, as R's fisher.test reports."""

    def test_matches_scipy_conditional(self):
        result = fisher_2x2(10, 10, 20, 60)
        reference = odds_ratio([[10, 10], [20, 60]], kind="conditional")
        interval = reference.confidence_interval()
        assert result.odds_ratio == pytest.approx(reference.statistic, rel=1e-8)
        assert result.ci_lower == pytest.approx(interval.low, rel=1e-8)
        assert result.ci_upper == pytest.approx(interval.high, rel=1e-8)
        assert result.p_value == pytest.approx(
            stats.fisher_exact([[10, 10], [20, 60]])[1],
        )

    def test_interval_and_p_value_agree_on_significance(self):
        for table in ((3, 7, 9, 81), (5, 5, 10, 30), (8, 2, 30, 60), (2, 18, 40, 40)):
            result = fisher_2x2(*table)
            excludes_one = result.ci_lower > 1 or result.ci_upper < 1
            assert excludes_one == (result.p_value < 0.05), table

    def test_a_zero_cell_still_has_a_bound(self):
        result = fisher_2x2(10, 0, 5, 85)
        assert math.isinf(result.odds_ratio)
        assert result.ci_lower == pytest.approx(26.164, rel=1e-3)
        assert math.isinf(result.ci_upper)


class TestPairwise:
    def test_hand_computed_pair(self):
        comparison = cmp.compare([A, B], UNIVERSE, background="test")
        (pair,) = comparison.pairs
        assert (pair.n_a, pair.n_b, pair.overlap) == (20, 30, 10)
        assert pair.expected == pytest.approx(20 * 30 / 100)
        assert pair.fold == pytest.approx(10 / 6)
        assert pair.jaccard == pytest.approx(10 / 40)
        assert pair.overlap_coefficient == pytest.approx(10 / 20)
        # a=10, b=10, c=20, d=100-40=60
        fisher = fisher_2x2(10, 10, 20, 60)
        assert pair.p_value == pytest.approx(fisher.p_value)
        assert pair.odds_ratio == pytest.approx(fisher.odds_ratio)

    def test_one_fdr_per_pair_in_pair_order(self):
        comparison = cmp.compare([A, B, C], UNIVERSE, background="test")
        assert [(p.a, p.b) for p in comparison.pairs] == [(0, 1), (0, 2), (1, 2)]
        adjusted = cmp.benjamini_hochberg([p.p_value for p in comparison.pairs])
        assert [p.fdr for p in comparison.pairs] == pytest.approx(adjusted)

    def test_sets_are_restricted_to_the_background(self):
        universe = frozenset(str(i) for i in range(15))
        comparison = cmp.compare([A, B], universe, background="small")
        assert [len(s.genes) for s in comparison.sets] == [15, 5]
        assert comparison.original_sizes == (20, 30)
        assert comparison.dropped == (5, 25)
        assert comparison.universe_size == 15

    @pytest.mark.parametrize("n", [1, 13])
    def test_the_number_of_sets_is_bounded(self, n):
        sets = [gene_set(str(i), [i]) for i in range(n)]
        with pytest.raises(ValueError, match="between"):
            cmp.compare(sets, UNIVERSE, background="test")


class TestBenjaminiHochberg:
    def test_known_values(self):
        # Sorted: 0.01*4/1, 0.03*4/2, 0.04*4/3, 0.2 -- then the running minimum
        # from the top carries 0.0533 down onto 0.03.
        assert cmp.benjamini_hochberg([0.01, 0.04, 0.03, 0.2]) == pytest.approx(
            [0.04, 0.16 / 3, 0.16 / 3, 0.2],
        )

    def test_matches_scipy(self):
        raw = [0.001, 0.2, 0.03, 0.04, 0.5, 0.012, 0.9]
        assert cmp.benjamini_hochberg(raw) == pytest.approx(
            list(stats.false_discovery_control(raw, method="bh")),
        )

    def test_monotone_in_the_raw_p_values_and_capped(self):
        raw = [0.5, 0.001, 0.9, 0.02, 0.6]
        adjusted = cmp.benjamini_hochberg(raw)
        order = sorted(range(len(raw)), key=raw.__getitem__)
        values = [adjusted[i] for i in order]
        assert values == sorted(values)
        assert max(adjusted) <= 1.0

    def test_nan_is_left_out_of_the_count(self):
        out = cmp.benjamini_hochberg([0.01, float("nan"), 0.02])
        assert math.isnan(out[1])
        assert out[0] == pytest.approx(0.02)
        assert out[2] == pytest.approx(0.02)


class TestRegions:
    def test_partition_the_union(self):
        comparison = cmp.compare([A, B, C], UNIVERSE, background="test")
        seen: set[str] = set()
        for region in comparison.regions:
            assert not seen & region.genes
            seen |= region.genes
        assert seen == comparison.union

    def test_membership_is_exact(self):
        comparison = cmp.compare([A, B, C], UNIVERSE, background="test")
        sizes = {r.members: r.size for r in comparison.regions}
        assert sizes == {(0,): 10, (0, 1): 10, (1,): 15, (1, 2): 5, (2,): 5}

    def test_largest_first(self):
        comparison = cmp.compare([A, B, C], UNIVERSE, background="test")
        sizes = [r.size for r in comparison.regions]
        assert sizes == sorted(sizes, reverse=True)

    def test_labels(self):
        comparison = cmp.compare([A, B], UNIVERSE, background="test")
        assert comparison.region_label((0,)) == "A only"
        assert comparison.region_label((0, 1)) == "A & B (all)"


class TestConstruction:
    FIELDS: ClassVar[dict[str, FieldMeta]] = {
        "flag_union": FieldMeta(
            fullname="U",
            derived_op="union",
            derived_from=["flag_x", "flag_y"],
        ),
        "flag_x": FieldMeta(fullname="X"),
        "flag_y": FieldMeta(fullname="Y"),
        "flag_top": FieldMeta(
            fullname="Top",
            derived_op="union",
            derived_from=["flag_union", "other"],
        ),
        "flag_ext": FieldMeta(
            fullname="Ext",
            derived_op="external",
            derived_from=["flag_x"],
        ),
        "ndd_v2": FieldMeta(fullname="NDD v2", aliases=["ndd"]),
        "ndd_v1": FieldMeta(fullname="NDD v1"),
        "other": FieldMeta(fullname="Other"),
    }
    ALIASES: ClassVar[dict[str, str]] = {"ndd": "ndd_v2"}

    def reasons(self, *columns: str) -> dict[tuple[int, int], str]:
        sets = [gene_set(c, [], column=c) for c in columns]
        return cmp.construction_reasons(sets, self.FIELDS, self.ALIASES)

    def test_derived_from_either_side(self):
        assert self.reasons("flag_x", "flag_union") == {
            (0, 1): "flag_union is derived from flag_x",
        }
        assert self.reasons("flag_union", "flag_y") == {
            (0, 1): "flag_union is derived from flag_y",
        }

    def test_derived_through_another_list(self):
        """hc_ndd_v7 against SFARI: a union over a union over the source."""
        assert self.reasons("flag_top", "flag_x") == {
            (0, 1): "flag_top is derived from flag_x (through U)",
        }

    def test_an_external_derivation_counts_too(self):
        assert self.reasons("flag_ext", "flag_x") == {
            (0, 1): "flag_ext is derived from flag_x",
        }

    def test_sharing_a_parent_is_not_construction(self):
        """Two lists built from one source need not overlap: no flag."""
        assert self.reasons("flag_union", "flag_ext") == {}

    def test_a_cycle_does_not_loop(self):
        fields = {
            "p": FieldMeta(fullname="P", derived_op="external", derived_from=["q"]),
            "q": FieldMeta(fullname="Q", derived_op="external", derived_from=["p"]),
        }
        sets = [gene_set(c, [], column=c) for c in ("p", "q")]
        assert cmp.construction_reasons(sets, fields, {}) == {
            (0, 1): "q is derived from p",
        }

    def test_versions_of_one_family(self):
        assert self.reasons("ndd_v1", "ndd_v2") == {
            (0, 1): "two versions of the ndd gene list",
        }

    def test_unrelated_and_user_sets_are_not_flagged(self):
        assert self.reasons("other", "ndd_v1") == {}
        sets = [gene_set("mine", []), gene_set("ndd_v1", [], column="ndd_v1")]
        assert cmp.construction_reasons(sets, self.FIELDS, self.ALIASES) == {}

    def test_flags_reach_the_pairs(self):
        sets = [gene_set(c, range(5), column=c) for c in ("ndd_v1", "ndd_v2", "other")]
        reasons = cmp.construction_reasons(sets, self.FIELDS, self.ALIASES)
        comparison = cmp.compare(sets, UNIVERSE, background="t", construction=reasons)
        flagged = [(p.a, p.b) for p in comparison.pairs if p.construction]
        assert flagged == [(0, 1)]


class TestBackgrounds:
    def test_every_gene_without_a_file(self, tmp_path: Path):
        assert load_backgrounds(tmp_path / "missing.yaml", []) == ([EVERY_GENE], [])

    def test_bad_entries_are_dropped_with_a_reason(self):
        raw = {
            "backgrounds": [
                {"name": "Coding", "column": "kind", "equals": "coding"},
                {"name": "Ghost", "column": "nope", "equals": 1},
                {"name": "Coding", "column": "kind", "equals": "x"},
                {"column": "kind", "equals": "x"},
                {"name": "Half", "column": "kind"},
                {"name": "Typo", "colum": "kind"},
                {"name": "All"},
            ],
        }
        backgrounds, problems = parse_backgrounds(raw, ["kind"])
        assert [b.name for b in backgrounds] == ["Coding", "All"]
        assert len(problems) == 5

    def test_an_empty_file_still_yields_every_gene(self):
        backgrounds, problems = parse_backgrounds({"backgrounds": []}, [])
        assert backgrounds == [EVERY_GENE]
        assert problems == []

    def test_expr_selects_and_never_keeps_nulls(self):
        frame = pl.DataFrame({"kind": ["coding", None, "pseudo", "coding"]})
        background = Background("Coding", "kind", "coding")
        assert frame.filter(background.expr()).height == 2
        assert frame.filter(EVERY_GENE.expr()).height == 4

    def test_the_shipped_file_parses(self):
        root = Path(__file__).parent.parent / "resources"
        path = root / "config" / "backgrounds.yaml"
        if not path.is_file():
            pytest.skip("no shipped backgrounds.yaml")
        backgrounds, problems = load_backgrounds(path, ["hgnc_locus_group"])
        assert problems == []
        assert backgrounds[-1].column is None

    def test_the_loader_exposes_them(self, resources):
        config = load_config(resources.root)
        assert config.backgrounds == [EVERY_GENE]


class TestEnrichmentAgrees:
    def test_the_drawer_and_compare_give_one_answer(self, resources):
        db = Database.open(resources.root)
        frame, key = db.frame, db.roles.primary_key
        results = frame.filter(pl.col("flag_b"))
        old = compute_enrichment(frame, results, "flag_a", db.roles)

        universe = cmp.keys_where(frame[key])
        sets = [
            gene_set("results", results[key].to_list()),
            gene_set("flag_a", frame.filter(pl.col("flag_a"))[key].to_list()),
        ]
        (pair,) = cmp.compare(sets, universe, background="all").pairs
        assert pair.overlap == old.a
        assert pair.p_value == pytest.approx(old.p_value)
        assert pair.odds_ratio == pytest.approx(old.odds_ratio)

    def test_a_background_restricts_both_sides(self, resources):
        db = Database.open(resources.root)
        frame = db.frame
        coding = Background("Coding", "gene_type", "protein-coding")
        result = compute_enrichment(
            frame,
            frame.filter(pl.col("flag_b")),
            "flag_a",
            db.roles,
            coding,
        )
        assert (
            result.a + result.b + result.c + result.d
            == frame.filter(
                coding.expr(),
            ).height
        )


class TestCovariates:
    def test_parsed_and_validated(self):
        raw = {
            "covariates": [
                {"name": "LOEUF decile", "column": "loeuf", "bins": 10},
                {"name": "Ghost", "column": "nope"},
                {"name": "Thin", "column": "loeuf", "bins": 1},
                {"name": "LOEUF decile", "column": "loeuf"},
            ],
        }
        covariates, problems = parse_covariates(raw, ["loeuf"])
        assert [c.name for c in covariates] == ["LOEUF decile"]
        assert len(problems) == 3

    def test_none_declared(self):
        assert parse_covariates({"backgrounds": []}, []) == ([], [])

    def test_the_shipped_file_declares_loeuf(self):
        path = (
            Path(__file__).parent.parent / "resources" / "config" / "backgrounds.yaml"
        )
        if not path.is_file():
            pytest.skip("no shipped backgrounds.yaml")
        covariates, problems = load_covariates(path, ["loeuf"])
        assert problems == []
        assert covariates[0].column == "loeuf"


class TestStratified:
    FRAME = pl.DataFrame(
        {
            "key": [str(i) for i in range(100)],
            "score": [float(i) if i < 90 else None for i in range(100)],
        },
    )

    def test_strata_are_quantiles_plus_missing(self):
        strata = cmp.strata_for(self.FRAME, "key", "score", 3)
        counts: dict[str, int] = {}
        for label in strata.values():
            counts[label] = counts.get(label, 0) + 1
        assert counts == {"q1": 30, "q2": 30, "q3": 30, "none": 10}

    def test_adjusted_results_sit_beside_the_crude(self):
        strata = cmp.strata_for(self.FRAME, "key", "score", 3)
        comparison = cmp.compare(
            [A, B, C],
            UNIVERSE,
            background="t",
            strata=strata,
            covariate="score tertile",
        )
        assert comparison.covariate == "score tertile"
        for pair in comparison.pairs:
            assert pair.adjusted is not None
            assert pair.adjusted.strata >= 1
        adjusted_fdr = cmp.benjamini_hochberg(
            [p.adjusted.p_value for p in comparison.pairs if p.adjusted],
        )
        assert [
            p.adjusted.fdr for p in comparison.pairs if p.adjusted
        ] == pytest.approx(
            adjusted_fdr,
            nan_ok=True,
        )

    def test_no_strata_no_adjustment(self):
        comparison = cmp.compare([A, B], UNIVERSE, background="t")
        assert comparison.covariate == ""
        assert comparison.pairs[0].adjusted is None

    def test_inputs_keep_the_unrestricted_sets(self):
        universe = frozenset(str(i) for i in range(15))
        comparison = cmp.compare([A, B], universe, background="t")
        assert comparison.inputs[1].genes == B.genes
        assert comparison.sets[1].genes == B.genes & universe


def test_constraint_adjustment_on_the_real_database():
    """hc_ndd_v7 x SynGO within protein-coding genes: crude OR ~2.8, ~1.74 by
    LOEUF decile. The number the paper's R3 quotes."""
    try:
        config = load_config()
    except SystemExit:
        pytest.skip("no real database")
    if not config.covariates or "syngo_v1_3" not in config.df.columns:
        pytest.skip("real database lacks the columns")
    key = config.roles.primary_key
    background = config.backgrounds[0]
    frame = config.df.filter(background.expr())
    covariate = config.covariates[0]
    sets = [
        cmp.GeneSet(
            c, c, c, "database", cmp.keys_where(config.df.filter(pl.col(c))[key]), c
        )
        for c in ("hc_ndd_v7", "syngo_v1_3")
    ]
    comparison = cmp.compare(
        sets,
        cmp.keys_where(frame[key]),
        background=background.name,
        strata=cmp.strata_for(frame, key, covariate.column, covariate.bins),
        covariate=covariate.name,
    )
    (pair,) = comparison.pairs
    assert pair.odds_ratio == pytest.approx(2.79, abs=0.02)
    assert pair.adjusted is not None
    assert pair.adjusted.odds_ratio == pytest.approx(1.74, abs=0.02)


class TestCoverageUniverse:
    """A gene a set did not measure is unknown, never "not in the set".

    One joint universe per comparison: the background less every gene some
    set did not measure, so the regions and every statistic count the same
    genes.
    """

    # M measured only genes 0..59 and is True on 0..9: 40 background genes
    # are unknown to it.
    M = cmp.GeneSet(
        id="db:m",
        label="M",
        name="M",
        kind="database",
        genes=frozenset(str(i) for i in range(10)),
        column="m",
        measured=frozenset(str(i) for i in range(60)),
    )

    def test_the_universe_is_the_background_less_the_unmeasured(self):
        comparison = cmp.compare([A, B, self.M], UNIVERSE, background="All")
        assert comparison.background_size == 100
        assert comparison.universe_size == 60
        assert comparison.coverage_loss == 40
        assert comparison.restricted_by == ("M",)

    def test_every_pair_shrinks_not_only_the_partial_one(self):
        comparison = cmp.compare([A, B, self.M], UNIVERSE, background="All")
        ab = next(p for p in comparison.pairs if (p.a, p.b) == (0, 1))
        expected = fisher_2x2(10, 10, 20, 60 - 40)
        assert ab.p_value == pytest.approx(expected.p_value)

    def test_regions_count_the_same_genes_as_the_statistics(self):
        comparison = cmp.compare([A, B, self.M], UNIVERSE, background="All")
        for pair in comparison.pairs:
            in_both = sum(
                r.size
                for r in comparison.regions
                if pair.a in r.members and pair.b in r.members
            )
            assert in_both == pair.overlap

    def test_dropped_genes_are_split_by_cause(self):
        wide = gene_set("W", range(50, 120))  # 20 outside, 30 unmeasured by M
        comparison = cmp.compare([wide, self.M], UNIVERSE, background="All")
        assert comparison.outside_background == (20, 0)
        assert comparison.not_measured == (40, 0)
        assert comparison.dropped == (60, 0)

    def test_full_coverage_changes_nothing(self):
        plain = cmp.compare([A, B], UNIVERSE, background="All")
        assert plain.universe_size == plain.background_size == 100
        assert plain.restricted_by == ()
        assert plain.coverage_warning == ""

    def test_a_large_loss_warns_and_names_the_set(self):
        comparison = cmp.compare([A, self.M], UNIVERSE, background="All")
        assert "M" in comparison.coverage_warning
        assert "60 of the 100" in comparison.coverage_warning

    def test_strata_follow_the_analysed_universe(self):
        frame = pl.DataFrame(
            {"key": [str(i) for i in range(100)], "x": [float(i) for i in range(100)]},
        )
        analysed = cmp.effective_universe([A, self.M], UNIVERSE)
        strata = cmp.strata_for(
            frame.filter(pl.col("key").is_in(list(analysed))),
            "key",
            "x",
            2,
        )
        # Halves of the 60 measured genes, not of the 100 in the background.
        assert sum(1 for s in strata.values() if s == "q1") == 30

    def test_measured_keys_is_none_without_nulls(self):
        frame = pl.DataFrame({"k": [1, 2, 3], "full": [True, False, True]})
        assert cmp.measured_keys(frame, "full", "k") is None
        frame = frame.with_columns(part=pl.Series([True, None, False]))
        assert cmp.measured_keys(frame, "part", "k") == frozenset({"1", "3"})


class TestEnrichmentCoverage:
    def test_unmeasured_genes_leave_the_universe(self):
        df = pl.DataFrame(
            {
                "ncbi_id": list(range(10)),
                "flag": [True, True, False, False, None, None, None, False, True, None],
            },
        )
        result = compute_enrichment(
            df,
            df.filter(pl.col("ncbi_id") < 4),
            "flag",
            Roles(primary_key="ncbi_id"),
        )
        assert result.n_not_measured == 4
        assert result.a + result.b + result.c + result.d == 6
        assert (result.a, result.b, result.c, result.d) == (2, 2, 1, 1)
