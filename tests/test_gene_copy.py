"""Tests for the text every "Copy genes" button puts on the clipboard."""

from __future__ import annotations

import polars as pl
import pytest

from genetrek.schema import Roles
from genetrek.services.gene_copy import copy_roles, gene_text
from genetrek.ui.components.column_info_dialog import list_members

FRAME = pl.DataFrame(
    {
        "key": ["HGNC:3", "HGNC:1", "HGNC:2", "HGNC:4"],
        "sym": ["SHANK3", "CHD8", "MECP2", "SCN2A"],
        "ens": ["ENSG03", None, "ENSG02", " "],
        "on_list": [True, None, False, True],
    },
)

ROLES = Roles(primary_key="key", symbol="sym", hgnc="key", ensg="ens")


class TestGeneText:
    def test_symbols_one_per_line_in_the_frame_order(self):
        """The table's order, which is the user's sort, not the database's."""
        result = gene_text(FRAME, ROLES, "symbol")
        assert result.text == "SHANK3\nCHD8\nMECP2\nSCN2A"
        assert result.copied == 4
        assert result.without_id == 0

    def test_genes_without_the_identifier_are_counted_not_guessed(self):
        result = gene_text(FRAME, ROLES, "ensg")
        assert result.text == "ENSG03\nENSG02"
        assert result.copied == 2
        assert result.without_id == 2

    def test_a_repeated_identifier_is_copied_once(self):
        frame = pl.DataFrame({"key": ["1", "2"], "sym": ["PCDHA1", "PCDHA1"]})
        result = gene_text(frame, Roles(primary_key="key", symbol="sym"), "symbol")
        assert result.text == "PCDHA1"
        assert result.without_id == 0

    def test_an_empty_frame_copies_nothing(self):
        result = gene_text(FRAME.clear(), ROLES, "symbol")
        assert (result.text, result.copied, result.without_id) == ("", 0, 0)

    @pytest.mark.parametrize("role", ["ncbi", "omim", "primary_key", "nope"])
    def test_a_role_it_cannot_copy_is_refused(self, role):
        with pytest.raises(ValueError, match="cannot copy"):
            gene_text(FRAME, ROLES, role)

    def test_the_choices_are_the_filled_roles_symbol_first(self):
        assert copy_roles(ROLES) == [
            ("symbol", "symbols"),
            ("ensg", "Ensembl ids"),
            ("hgnc", "HGNC ids"),
        ]
        assert copy_roles(Roles()) == []


class TestListMembers:
    def test_a_gene_list_is_its_true_rows(self):
        """Null is "not measured": not on the list, like False."""
        members = list_members(FRAME, "on_list")
        assert members is not None
        assert members["sym"].to_list() == ["SHANK3", "SCN2A"]

    @pytest.mark.parametrize("column", ["sym", "missing"])
    def test_anything_else_is_not_a_list(self, column):
        assert list_members(FRAME, column) is None
