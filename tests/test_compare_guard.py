"""Backgrounds beyond the presets, covariates beyond one, and the covariate guard.

A list cut from the covariate itself -- a LOEUF threshold adjusted for LOEUF --
has no meaningful adjusted result: within each stratum it is all or nothing.
The guard withholds the adjusted result when the metadata says so, and flags it
when the data shows the covariate deciding most of a set's membership.
"""

from __future__ import annotations

import random

import polars as pl
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from genetrek_server import backgrounds as bg
from genetrek_server.api import compare_routes
from genetrek_server.api.routes import create_router
from genetrek_server.backgrounds import (
    Background,
    Condition,
    Covariate,
    background_warning,
    gene_list_background,
    parse_backgrounds,
    parse_combinations,
    parse_covariates,
    user_background,
)
from genetrek_server.config.loader import load_config
from genetrek_server.db import Database
from genetrek_server.paths import ENV_VAR
from genetrek_server.schema import FieldMeta
from genetrek_server.services import compare as cmp
from genetrek_server.services import compare_export as export

# -- configuration --------------------------------------------------------------


class TestBackgroundConfig:
    def test_where_ands_its_conditions(self):
        raw = {
            "backgrounds": [
                {
                    "name": "Brain coding",
                    "where": [
                        {"column": "kind", "equals": "coding"},
                        {"column": "brain", "equals": True},
                    ],
                },
            ],
        }
        (background,), problems = parse_backgrounds(raw, ["kind", "brain"])
        assert problems == []
        frame = pl.DataFrame(
            {
                "kind": ["coding", "coding", "rna", "coding"],
                "brain": [True, False, True, None],
            },
        )
        assert frame.filter(background.expr()).height == 1
        assert "kind is 'coding' and brain is True" in background.describe()

    @pytest.mark.parametrize(
        ("where", "fragment"),
        [
            ([{"column": "nope", "equals": 1}], "not in the data"),
            ([{"column": "kind"}], "exactly column and equals"),
            ("kind", "must be a list"),
        ],
    )
    def test_a_bad_where_is_dropped(self, where, fragment):
        raw = {"backgrounds": [{"name": "B", "where": where}]}
        backgrounds, problems = parse_backgrounds(raw, ["kind"])
        assert backgrounds == [bg.EVERY_GENE]
        assert fragment in problems[0]


SPANS = {"hg38": ("hg38_start", "hg38_end")}


class TestCovariateConfig:
    def test_a_span_is_the_gene_length(self):
        raw = {"covariates": [{"name": "Length", "span": "hg38", "bins": 4}]}
        (covariate,), problems = parse_covariates(raw, [], SPANS)
        assert problems == []
        frame = pl.DataFrame({"hg38_start": [100, 1_000], "hg38_end": [150, 4_000]})
        assert frame.select(covariate.expr()).to_series().to_list() == [50.0, 3000.0]
        assert "gene length (hg38)" in covariate.describe()

    @pytest.mark.parametrize(
        ("entry", "fragment"),
        [
            ({"name": "X", "span": "hg19"}, "no coordinate columns"),
            ({"name": "X", "column": "loeuf", "span": "hg38"}, "exactly one of"),
            ({"name": "X"}, "exactly one of"),
            ({"name": "X", "column": "loeuf", "joint_bins": 1}, "joint_bins"),
        ],
    )
    def test_a_bad_covariate_is_dropped(self, entry, fragment):
        covariates, problems = parse_covariates(
            {"covariates": [entry]}, ["loeuf"], SPANS
        )
        assert covariates == []
        assert fragment in problems[0]

    def test_combinations_name_two_known_covariates(self):
        covariates = [Covariate("A", "a"), Covariate("B", "b")]
        good, problems = parse_combinations({"combinations": [["A", "B"]]}, covariates)
        assert good == [("A", "B")]
        assert problems == []
        bad, problems = parse_combinations(
            {"combinations": [["A"], ["A", "A"], ["A", "C"]]}, covariates
        )
        assert bad == []
        assert len(problems) == 3


class TestBackgroundKinds:
    def test_given_genes_need_the_key(self):
        background = gene_list_background("NDD", frozenset({"1", "2"}))
        frame = pl.DataFrame({"id": [1, 2, 3]})
        assert frame.filter(background.expr("id"))["id"].to_list() == [1, 2]
        with pytest.raises(ValueError, match="needs the key"):
            background.expr()
        assert "database gene list" in background.describe()

    def test_a_user_background_has_a_floor(self):
        with pytest.raises(ValueError, match="at least 200"):
            user_background("tiny", frozenset({"1"}))
        panel = user_background("Panel", frozenset(str(i) for i in range(300)))
        assert "user-supplied gene list" in panel.describe()
        assert "300 genes" in panel.describe()
        assert "only 300 genes" in background_warning(panel)

    def test_presets_keep_the_shorthand(self):
        preset = Background("Coding", "kind", "coding")
        assert preset.conditions == (Condition("kind", "coding"),)
        assert background_warning(preset) == ""


# -- strata ---------------------------------------------------------------------


def test_strata_from_an_expression():
    frame = pl.DataFrame(
        {"id": ["a", "b", "c", "d"], "start": [0, 0, 0, None], "end": [10, 20, 30, 5]},
    )
    strata = cmp.strata_for(frame, "id", pl.col("end") - pl.col("start"), 3)
    assert strata == {"a": "q1", "b": "q2", "c": "q3", "d": "none"}


def test_joint_strata_cross_and_keep_none():
    first = {"a": "q1", "b": "q2", "c": "none"}
    second = {"a": "q3", "b": "none", "c": "q1"}
    assert cmp.joint_strata([first, second]) == {
        "a": "q1|q3",
        "b": "none",
        "c": "none",
    }
    assert cmp.joint_strata([first]) == first


# -- the guard ------------------------------------------------------------------

N = 1000
GENES = [str(i) for i in range(N)]
UNIVERSE = frozenset(GENES)
#: Deciles of a covariate equal to the gene number: decile k holds 100 genes.
STRATA = {g: f"q{int(g) // 100 + 1}" for g in GENES}


def _sets(*members: frozenset[str], columns: tuple[str | None, ...] = ()) -> list:
    columns = columns or (None,) * len(members)
    return [
        cmp.GeneSet(f"s{i}", f"S{i}", f"S{i}", "my_list", genes, column=column)
        for i, (genes, column) in enumerate(zip(members, columns, strict=True))
    ]


def test_a_set_cut_from_the_covariate_is_flagged():
    cut = frozenset(GENES[:200])  # the two lowest deciles, whole
    rng = random.Random(7)  # noqa: S311 -- seeded test data
    spread = frozenset(rng.sample(GENES, 200))
    assert (
        cmp.filled_share(
            cut,
            {
                k: frozenset(g for g, s in STRATA.items() if s == k)
                for k in set(STRATA.values())
            },
        )
        == 1.0
    )
    result = cmp.compare(
        _sets(cut, spread),
        UNIVERSE,
        background="All",
        strata=STRATA,
        covariate="Score decile",
    )
    (pair,) = result.pairs
    assert pair.adjusted is not None
    assert "largely determines S0" in pair.adjusted_note
    assert any("S0" in note for note in result.adjustment_notes)


def test_a_set_spread_over_the_strata_is_not():
    rng = random.Random(11)  # noqa: S311 -- seeded test data
    a, b = (frozenset(rng.sample(GENES, 150)) for _ in range(2))
    result = cmp.compare(
        _sets(a, b), UNIVERSE, background="All", strata=STRATA, covariate="Score"
    )
    assert result.pairs[0].adjusted_note == ""
    assert result.adjustment_notes == ()


def test_a_set_defined_from_the_covariate_gets_no_adjusted_result():
    fields = {
        "flag": FieldMeta(
            fullname="Flag", derived_op="external", derived_from=["score"]
        ),
        "roll": FieldMeta(
            fullname="Roll", derived_op="external", derived_from=["flag"]
        ),
        "other": FieldMeta(fullname="Other"),
    }
    rng = random.Random(3)  # noqa: S311 -- seeded test data
    sets = _sets(
        *(frozenset(rng.sample(GENES, 120)) for _ in range(3)),
        columns=("roll", "other", None),
    )
    defined = cmp.defined_from(sets, fields, ["score"])
    assert defined == {0: "score"}  # through flag, two steps up
    result = cmp.compare(
        sets,
        UNIVERSE,
        background="All",
        strata=STRATA,
        covariate="Score decile",
        defined=defined,
    )
    by_pair = {(p.a, p.b): p for p in result.pairs}
    assert by_pair[(0, 1)].adjusted is None
    assert "circular" in by_pair[(0, 1)].adjusted_note
    assert by_pair[(1, 2)].adjusted is not None
    # One computed test, so its FDR is its own p-value.
    assert by_pair[(1, 2)].adjusted.fdr == pytest.approx(
        by_pair[(1, 2)].adjusted.p_value
    )


def test_methods_text_and_request_carry_the_choices():
    rng = random.Random(5)  # noqa: S311 -- seeded test data
    sets = _sets(*(frozenset(rng.sample(GENES, 100)) for _ in range(2)))
    result = cmp.compare(
        sets,
        UNIVERSE,
        background="Panel",
        strata=cmp.joint_strata([STRATA, STRATA]),
        covariate="A + B",
        covariate_names=["A", "B"],
        background_spec={"label": "Panel", "genes": ["1", "2"]},
    )
    body = export.request_body(result)
    assert body["adjust_for"] == ["A", "B"]
    assert body["background"] == {"label": "Panel", "genes": ["1", "2"]}
    from datetime import date  # noqa: PLC0415

    text = export.methods_text(
        result, release="r", background_definition="x", today=date(2026, 11, 4)
    )
    assert "the combinations of A + B" in text


# -- the API --------------------------------------------------------------------

CONFIG = {
    "backgrounds": [
        {"name": "Coding", "column": "gene_type", "equals": "protein-coding"},
        {"name": "All genes"},
    ],
    "covariates": [
        {"name": "Score tertile", "column": "score", "bins": 3},
        {"name": "Length", "span": "hg38", "bins": 3, "joint_bins": 2},
    ],
    "combinations": [["Score tertile", "Length"]],
}


@pytest.fixture
def client(aliased_resources, monkeypatch):
    (aliased_resources.root / "config" / "backgrounds.yaml").write_text(
        yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8"
    )
    # Mark flag_b as cut from score, as the constraint flags are from LOEUF.
    database = Database.open(aliased_resources.root)
    database.set_meta("flag_b", derived_op="external", derived_from=["score"])
    database.save()
    # The synthetic tree has 20 genes: lower the floor so a list can serve.
    monkeypatch.setattr(compare_routes, "MIN_USER_BACKGROUND", 3)
    monkeypatch.setattr(bg, "MIN_USER_BACKGROUND", 3)
    monkeypatch.setenv(ENV_VAR, str(aliased_resources.root))
    app = FastAPI()
    app.include_router(create_router(load_config(aliased_resources.root)))
    return TestClient(app)


def test_backgrounds_lists_spans_and_combinations(client):
    body = client.get("/api/backgrounds").json()
    length = next(c for c in body["covariates"] if c["name"] == "Length")
    assert (length["column"], length["span"], length["joint_bins"]) == ("", "hg38", 2)
    assert body["combinations"] == [["Score tertile", "Length"]]


def test_a_database_list_as_background(client):
    body = client.post(
        "/api/compare",
        json={
            "sets": [{"field": "flag_b"}, {"field": "flag_a"}],
            "background": {"field": "flag"},
        },
    ).json()
    assert body["background"]["kind"] == "gene_list"
    assert body["background"]["size"] == 10  # flag_a: the even ids


def test_genes_as_background_with_two_covariates(client):
    response = client.post(
        "/api/compare",
        json={
            "sets": [{"genes": ["GENE02", "GENE04"]}, {"genes": ["GENE04", "GENE06"]}],
            "background": {
                "label": "Panel",
                "genes": [f"GENE{i:02d}" for i in range(2, 12)],
            },
            "adjust_for": ["Score tertile", "Length"],
        },
    )
    body = response.json()
    assert response.status_code == 200, body
    assert body["background"]["kind"] == "user"
    assert body["background"]["name"] == "Panel"
    assert body["adjusted_for"] == ["Score tertile + Length"]
    assert any("only 10 genes" in w for w in body["warnings"])


def test_a_set_cut_from_the_covariate_is_withheld(client):
    body = client.post(
        "/api/compare",
        json={
            "sets": [{"field": "flag_b"}, {"field": "flag_a"}],
            "background": "All genes",
        },
    ).json()
    (pair,) = body["pairs"]
    assert pair["adjusted"] is None
    assert "circular" in pair["adjusted_unavailable"]
    assert any("circular" in w for w in body["warnings"])
    assert "checked for circularity" in body["methods"]


def test_a_tiny_background_is_refused(client, monkeypatch):
    monkeypatch.setattr(compare_routes, "MIN_USER_BACKGROUND", 200)
    monkeypatch.setattr(bg, "MIN_USER_BACKGROUND", 200)
    response = client.post(
        "/api/compare",
        json={
            "sets": [{"field": "flag_a"}, {"field": "flag_b"}],
            "background": {"genes": ["GENE02"]},
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_background"


def test_more_than_two_covariates_are_refused(client):
    response = client.post(
        "/api/compare",
        json={
            "sets": [{"field": "flag_a"}, {"field": "flag_b"}],
            "adjust_for": ["Score tertile", "Length", "Score tertile"],
        },
    )
    assert response.status_code == 422


def test_a_supplied_background_replays(client):
    request = {
        "sets": [{"field": "flag_a"}, {"genes": ["GENE02", "GENE03"]}],
        "background": {
            "label": "Panel",
            "genes": [f"GENE{i:02d}" for i in range(2, 12)],
        },
        "adjust_for": [],
    }
    first = client.post("/api/compare", json=request).json()
    again = client.post("/api/compare", json=request).json()
    assert first["pairs"] == again["pairs"]
