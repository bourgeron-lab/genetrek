"""Contracts of the lookup endpoints: fields, genes, gene lists, conversion.

The synthetic tree is what makes several of these testable at all. Its
``symbol`` column repeats ``GENE01`` on the last row, so ambiguity is
reachable -- on the real database both the symbol and the primary key are
unique across all 45,045 genes. Its primary key is ``ncbi_id`` where the real
one is ``hgnc_id``, so anything that reads a key from a literal rather than
from ``roles`` fails here. And ``loeuf`` is hidden in both, which is the whole
reason the visibility policy exists.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from genetrek.api.routes import create_router
from genetrek.config.loader import load_config
from genetrek.paths import ENV_VAR

#: What the base fixture makes visible, in display order.
VISIBLE = ["symbol", "flag_a", "flag_b", "gene_type", "note", "score", "small_count"]


def make_client(root, monkeypatch) -> TestClient:
    """A client over one resources tree, with no NiceGUI mount involved."""
    monkeypatch.setenv(ENV_VAR, str(root))
    app = FastAPI()
    app.include_router(create_router(load_config(root)))
    return TestClient(app)


@pytest.fixture
def client(resources, monkeypatch) -> TestClient:
    return make_client(resources.root, monkeypatch)


@pytest.fixture
def aliased_client(aliased_resources, monkeypatch) -> TestClient:
    return make_client(aliased_resources.root, monkeypatch)


class TestFieldList:
    def test_lists_the_visible_columns_in_display_order(self, client):
        body = client.get("/api/fields").json()
        assert [f["name"] for f in body["fields"]] == VISIBLE
        assert body["total"] == len(VISIBLE)

    def test_the_shape_is_pinned(self, client):
        field = client.get("/api/fields").json()["fields"][0]
        assert set(field) == {
            "name",
            "fullname",
            "description",
            "category",
            "subcategory",
            "visibility",
            "value_type",
            "id_type",
            "assembly",
            "source",
            "doi",
            "licence",
            "universe",
            "update",
            "aliases",
            "derived_op",
            "derived_from",
            "n_genes",
        }

    def test_hidden_columns_are_omitted_until_asked_for(self, client):
        listed = {f["name"] for f in client.get("/api/fields").json()["fields"]}
        assert "loeuf" not in listed
        assert "ncbi_id" not in listed

        every = client.get("/api/fields?include_hidden=true").json()
        assert {"loeuf", "ncbi_id", "ensg"} <= {f["name"] for f in every["fields"]}

    def test_counts_are_opt_in(self, client):
        without = client.get("/api/fields").json()["fields"]
        assert all(f["n_genes"] is None for f in without)

        with_counts = client.get("/api/fields?include_counts=true").json()["fields"]
        counts = {f["name"]: f["n_genes"] for f in with_counts}
        # A boolean's count is its membership, not its row count: 10 even rows
        # for flag_a, 3 for flag_b. The 15 literal "None" strings in `note` are
        # a missing marker, leaving 5.
        assert counts["flag_a"] == 10
        assert counts["flag_b"] == 3
        assert counts["note"] == 5

    def test_filters_compose(self, client):
        body = client.get("/api/fields?value_type=boolean").json()
        assert [f["name"] for f in body["fields"]] == ["flag_a", "flag_b"]

        body = client.get("/api/fields?visibility=primary").json()
        assert [f["name"] for f in body["fields"]] == ["symbol", "flag_a"]

    def test_an_unmatched_filter_is_empty_not_an_error(self, client):
        body = client.get("/api/fields?category=Nonexistent").json()
        assert body == {"total": 0, "fields": []}


class TestFieldDetail:
    def test_reports_coverage_for_a_boolean(self, client):
        body = client.get("/api/fields/flag_b").json()
        assert body["field"] == "flag_b"
        assert body["requested_as"] == "flag_b"
        assert body["coverage"]["n_true"] == 3
        assert body["coverage"]["n_false"] == 17
        assert body["coverage"]["minimum"] is None

    def test_reports_coverage_for_a_number(self, client):
        coverage = client.get("/api/fields/score").json()["coverage"]
        assert coverage["minimum"] == pytest.approx(0.25)
        assert coverage["maximum"] == pytest.approx(5.0)
        assert coverage["n_true"] is None

    def test_counts_the_missing_value_marker_in_strings(self, client):
        coverage = client.get("/api/fields/note").json()["coverage"]
        assert coverage["n_missing_marker"] == 15
        assert coverage["top_values"]

    def test_a_hidden_column_is_reachable_by_name(self, client):
        """The point of the visibility policy: `loeuf` is hidden in real data."""
        body = client.get("/api/fields/loeuf")
        assert body.status_code == 200
        assert body.json()["metadata"]["visibility"] == "hidden"

    def test_an_unknown_field_is_a_404(self, client):
        response = client.get("/api/fields/nope")
        assert response.status_code == 404
        assert "nope" in response.json()["detail"]


class TestFieldGenes:
    def test_pairs_are_keyed_on_the_symbol_by_default(self, client):
        body = client.get("/api/fields/score/genes").json()
        assert body["id_type"] == "symbol"
        assert body["id_column"] == "symbol"
        assert body["total"] == 20
        assert body["genes"][0] == {"gene": "GENE01", "value": pytest.approx(0.25)}

    def test_drop_empty_on_a_boolean_returns_the_members(self, client):
        """A gene list's useful reading is its members, so False goes too."""
        body = client.get("/api/fields/flag_b/genes?drop_empty=true").json()
        assert body["total"] == 3
        assert [g["gene"] for g in body["genes"]] == ["GENE01", "GENE02", "GENE03"]
        assert all(g["value"] is True for g in body["genes"])

    def test_drop_empty_on_a_string_drops_the_missing_marker(self, client):
        body = client.get("/api/fields/note/genes?drop_empty=true").json()
        assert body["total"] == 5
        assert "None" not in [g["value"] for g in body["genes"]]

    def test_without_drop_empty_the_whole_column_comes_back(self, client):
        body = client.get("/api/fields/flag_b/genes").json()
        assert body["total"] == 20
        assert sum(g["value"] for g in body["genes"]) == 3

    def test_another_identifier_can_key_the_pairs(self, client):
        body = client.get("/api/fields/score/genes?id_type=ncbi").json()
        assert body["id_column"] == "ncbi_id"
        # Cast to string regardless of the column's dtype, which is Int32 here
        # and a prefixed string on the real database.
        assert body["genes"][0]["gene"] == "1"

    def test_genes_the_identifier_cannot_name_are_counted_not_returned(self, client):
        """A pair whose gene is null names nothing, so it is never a row."""
        body = client.get("/api/fields/score/genes?id_type=ensg").json()
        assert body["n_without_identifier"] == 6
        assert body["total"] == 14
        assert all(g["gene"] is not None for g in body["genes"])

    def test_an_identifier_role_the_database_lacks_is_a_400(self, client):
        response = client.get("/api/fields/score/genes?id_type=uniprot")
        assert response.status_code == 400
        assert "available roles" in response.json()["detail"]

    def test_the_column_can_be_its_own_identifier(self, client):
        """Selecting one column twice is a Polars error, and reachable."""
        body = client.get("/api/fields/symbol/genes?id_type=symbol")
        assert body.status_code == 200
        assert body.json()["genes"][0] == {"gene": "GENE01", "value": "GENE01"}

    def test_limit_and_offset_page_without_changing_the_total(self, client):
        body = client.get("/api/fields/score/genes?limit=3&offset=5").json()
        assert body["total"] == 20
        assert len(body["genes"]) == 3
        assert body["genes"][0]["gene"] == "GENE06"

    def test_the_tsv_names_the_real_columns(self, client):
        response = client.get("/api/fields/flag_b/genes/tsv?drop_empty=true")
        assert response.status_code == 200
        lines = response.text.strip().splitlines()
        assert lines[0] == "symbol\tflag_b"
        assert len(lines) == 4
        assert "attachment" in response.headers["content-disposition"]


class TestGeneLookup:
    def test_any_identifier_finds_the_gene(self, client):
        for query in ("GENE02", "2", "ENSG00000000002"):
            body = client.get(f"/api/genes/{query}").json()
            assert body["gene"]["key"] == "2"
            assert body["gene"]["key_column"] == "ncbi_id"
            assert body["identifiers"]["symbol"] == "GENE02"

    def test_identifiers_are_reported_by_role_with_their_columns(self, client):
        body = client.get("/api/genes/GENE02").json()
        assert body["id_columns"]["ncbi"] == "ncbi_id"
        assert body["identifiers"]["ncbi"] == 2

    def test_the_default_selection_is_the_visible_columns(self, client):
        body = client.get("/api/genes/GENE02").json()
        assert list(body["values"]) == VISIBLE

    def test_a_hidden_column_comes_back_when_named(self, client):
        """This is the LOEUF case, on a tree where loeuf is hidden as well."""
        body = client.get("/api/genes/GENE02?columns=loeuf").json()
        assert body["values"] == {"loeuf": pytest.approx(0.2)}

    def test_an_ambiguous_symbol_is_a_409_naming_the_candidates(self, client):
        """GENE01 is on rows 1 and 20, so no single answer exists."""
        response = client.get("/api/genes/GENE01")
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert "1" in detail
        assert "20" in detail

    def test_an_unknown_gene_is_a_404(self, client):
        assert client.get("/api/genes/NOTAGENE").status_code == 404

    def test_an_unknown_column_is_a_404(self, client):
        assert client.get("/api/genes/GENE02?columns=nope").status_code == 404

    def test_an_infinite_float_serialises_as_null_not_a_500(
        self, infinite_resources, monkeypatch
    ):
        """FastAPI coerces it; a hand-rolled JSONResponse would raise instead.

        The real database holds 37 infinite odds ratios, so an endpoint that
        can reach a float column can reach one of them.
        """
        client = make_client(infinite_resources.root, monkeypatch)
        # By key, not by symbol: GENE01 is the duplicated one in this tree.
        response = client.get("/api/genes/1?columns=loeuf")
        assert response.status_code == 200
        assert response.json()["values"] == {"loeuf": None}


class TestGeneGenesets:
    def test_lists_the_boolean_columns_that_are_true(self, client):
        body = client.get("/api/genes/GENE02/genesets").json()
        assert [g["name"] for g in body["genesets"]] == ["flag_a", "flag_b"]
        assert body["total"] == 2

    def test_a_gene_on_no_list_gets_an_empty_list_not_a_404(self, client):
        response = client.get("/api/genes/GENE05/genesets")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 0
        assert body["genesets"] == []
        assert body["gene"]["key"] == "5"

    def test_each_entry_carries_what_a_client_needs_to_filter(self, client):
        """Every boolean column counts, so the caller does the narrowing."""
        entry = client.get("/api/genes/GENE02/genesets").json()["genesets"][0]
        assert entry["category"] == ""
        assert entry["derived_from"] == []
        assert entry["n_genes"] == 10

    def test_the_gene_can_be_named_by_any_identifier(self, client):
        body = client.get("/api/genes/2/genesets").json()
        assert body["gene"]["matched_by"] == "ncbi_id"
        assert body["total"] == 2


class TestConvert:
    def test_converts_between_identifier_types(self, client):
        body = client.get("/api/convert?ids=GENE02&to=ncbi,ensg").json()
        assert body["results"] == [
            {
                "input": "GENE02",
                "matched_by": "symbol",
                "note": "",
                "ids": {"ncbi": 2, "ensg": "ENSG00000000002"},
            },
        ]

    def test_reports_what_it_could_not_resolve_rather_than_omitting_it(self, client):
        body = client.get("/api/convert?ids=GENE02,NOTAGENE&to=ncbi").json()
        assert body["n_input"] == 2
        assert body["n_resolved"] == 1
        assert body["results"][1]["matched_by"] == "unmatched"
        assert body["results"][1]["ids"] == {"ncbi": None}

    def test_input_order_is_preserved(self, client):
        ids = "GENE05,GENE02,NOTAGENE,GENE03"
        body = client.get(f"/api/convert?ids={ids}&to=ncbi").json()
        assert [r["input"] for r in body["results"]] == ids.split(",")

    def test_an_ambiguous_symbol_resolves_to_nothing_rather_than_a_guess(self, client):
        body = client.get("/api/convert?ids=GENE01&to=ncbi").json()
        assert body["results"][0]["matched_by"] == "ambiguous"
        assert body["results"][0]["ids"] == {"ncbi": None}

    def test_symbol_coverage_says_what_the_caller_actually_got(self, client):
        """No alias source in the synthetic tree, so retired symbols cannot."""
        body = client.get("/api/convert?ids=GENE02").json()
        assert body["symbol_coverage"] == "exact-only"
        assert any("retired" in w for w in body["warnings"])

    def test_defaults_to_every_role_the_database_has(self, client):
        body = client.get("/api/convert?ids=GENE02").json()
        assert set(body["id_columns"]) == {"symbol", "ensg", "ncbi", "hgnc", "omim"}

    def test_the_detected_type_is_reported(self, client):
        body = client.get("/api/convert?ids=GENE02,GENE03").json()
        assert body["requested_id_type"] == "auto"
        assert body["id_type"] == "symbol"
        assert body["detected"] is True

    def test_separators_are_interchangeable(self, client):
        body = client.get("/api/convert?ids=GENE02%20GENE03%3BGENE04").json()
        assert body["n_input"] == 3

    def test_an_empty_list_is_a_400(self, client):
        assert client.get("/api/convert?ids=%20").status_code == 400

    def test_too_many_identifiers_is_a_400_pointing_somewhere_useful(self, client):
        ids = ",".join(f"GENE{i}" for i in range(1001))
        response = client.get("/api/convert", params={"ids": ids})
        assert response.status_code == 400
        assert "/api/download/database" in response.json()["detail"]


class TestAliases:
    def test_a_field_can_be_reached_by_its_alias(self, aliased_client):
        body = aliased_client.get("/api/fields/flag").json()
        assert body["field"] == "flag_a"
        assert body["requested_as"] == "flag"

    def test_the_values_are_labelled_with_the_real_column(self, aliased_client):
        """Downloaded data must never be ambiguous about its own provenance."""
        body = aliased_client.get("/api/fields/flag/genes?drop_empty=true").json()
        assert body["field"] == "flag_a"
        assert body["requested_as"] == "flag"
        assert body["total"] == 10

        tsv = aliased_client.get("/api/fields/flag/genes/tsv").text
        assert tsv.splitlines()[0] == "symbol\tflag_a"

    def test_an_alias_reaches_a_hidden_column(self, aliased_client):
        body = aliased_client.get("/api/genes/GENE02?columns=constraint").json()
        assert body["values"] == {"loeuf": pytest.approx(0.2)}

    def test_aliases_are_listed_so_they_can_be_discovered(self, aliased_client):
        fields = aliased_client.get("/api/fields").json()["fields"]
        assert next(f for f in fields if f["name"] == "flag_a")["aliases"] == ["flag"]


class TestTheSchemaIsPublished:
    def test_the_new_paths_are_documented(self, client):
        published = client.get("/openapi.json").json()["paths"]
        assert {
            "/api/fields",
            "/api/fields/{field}",
            "/api/fields/{field}/genes",
            "/api/fields/{field}/genes/tsv",
            "/api/genes/{gene}",
            "/api/genes/{gene}/genesets",
            "/api/convert",
        } <= set(published)


class TestNotMeasured:
    """Null is "not measured": distinct from False everywhere it leaves the API."""

    @pytest.fixture
    def measured_client(self, measured_resources, monkeypatch) -> TestClient:
        return make_client(measured_resources.root, monkeypatch)

    def test_the_field_carries_its_universe_and_count(self, measured_client):
        body = measured_client.get("/api/fields/flag_m").json()
        assert body["metadata"]["universe"] == "Genes 1 to 12 (synthetic)"
        coverage = body["coverage"]
        assert (coverage["n_measured"], coverage["n_null"]) == (12, 8)
        assert coverage["n_true"] + coverage["n_false"] == coverage["n_measured"]

    def test_the_universe_is_the_measured_genes(self, measured_client):
        body = measured_client.get("/api/fields/flag_m/universe?id_type=ncbi").json()
        assert body["genes"] == [str(i) for i in range(1, 13)]
        assert (body["total"], body["n_not_measured"]) == (12, 8)
        assert body["universe"] == "Genes 1 to 12 (synthetic)"

    def test_the_universe_downloads_as_one_column(self, measured_client):
        tsv = measured_client.get("/api/fields/flag_m/universe/tsv?id_type=ncbi").text
        lines = tsv.splitlines()
        assert lines[0] == "ncbi_id"
        assert lines[1:] == [str(i) for i in range(1, 13)]

    def test_a_full_column_keeps_null_apart_from_false(self, measured_client):
        genes = measured_client.get("/api/fields/flag_m/genes?id_type=ncbi").json()
        values = {g["gene"]: g["value"] for g in genes["genes"]}
        assert (values["4"], values["5"], values["13"]) == (True, False, None)
        tsv = measured_client.get("/api/fields/flag_m/genes/tsv?id_type=ncbi").text
        rows = dict(line.split("\t") for line in tsv.splitlines()[1:])
        assert (rows["4"], rows["5"], rows["13"]) == ("true", "false", "NA")

    def test_drop_empty_still_means_members(self, measured_client):
        body = measured_client.get("/api/fields/flag_m/genes?drop_empty=true").json()
        assert body["total"] == 4
