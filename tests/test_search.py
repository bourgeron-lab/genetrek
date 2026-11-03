"""Tests for locus parsing in the gene search.

The module had no tests. These cover the token formats a *user* types or pastes,
because that is where the failures are silent: an unparseable locus is dropped
without complaint, and a mis-split one comes back as confident matches on the
wrong chromosome.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import pytest

from genetrek_server.config.loader import load_config
from genetrek_server.db.gene_index import GeneIndex
from genetrek_server.schema import LocusColumns, Roles
from genetrek_server.services import search as search_module
from genetrek_server.services.lookup import symbol_resolver
from genetrek_server.services.search import (
    MAX_LINK_TOKENS,
    filter_dataframe,
    link_note,
    parse_search_link,
    split_tokens,
    unmatched_tokens,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Three genes, on the chromosomes whose spelling is contentious.
FRAME = pl.DataFrame(
    {
        "key": ["1", "2", "3"],
        "sym": ["SHANK3", "MECP2", "MT-ATP6"],
        "chrom": ["22", "X", "M"],
        "start": [50674408, 154021573, 8527],
        "end": [50733212, 154137103, 9207],
    },
)

ROLES = Roles(
    primary_key="key",
    symbol="sym",
    assemblies={
        "GRCh38": LocusColumns(
            assembly="GRCh38",
            chrom="chrom",
            start="start",
            end="end",
        ),
    },
)

#: The same database, but with ``key`` also serving as the NCBI ID, so a bare
#: number is a live token. The separator tests need that: the bug they guard
#: turned coordinate fragments into NCBI IDs, which can only match when the
#: role is filled.
ROLES_WITH_NCBI = Roles(
    primary_key="key",
    symbol="sym",
    ncbi="key",
    assemblies=ROLES.assemblies,
)


def search(
    text: str,
    frame: pl.DataFrame = FRAME,
    roles: Roles = ROLES,
) -> list[str]:
    """Run the real filter and return the matched symbols."""
    return filter_dataframe(
        frame,
        text,
        exact_match=True,
        selected_bool_cols=[],
        results_merge="union",
        annotations_merge="union",
        selected_genome="GRCh38",
        roles=roles,
    )["sym"].to_list()


class TestLocusSpellings:
    @pytest.mark.parametrize(
        "token",
        [
            "22:50674408-50733212",
            "chr22:50674408-50733212",
            "CHR22:50674408-50733212",
            "Chr22:50674408-50733212",
        ],
    )
    def test_an_autosome_is_found_however_the_prefix_is_written(self, token):
        assert search(token) == ["SHANK3"]

    @pytest.mark.parametrize(
        "token",
        [
            "X:154021573-154137103",
            "chrX:154021573-154137103",
            "chrx:154021573-154137103",
        ],
    )
    def test_the_sex_chromosomes_are_found(self, token):
        assert search(token) == ["MECP2"]

    @pytest.mark.parametrize(
        "token",
        [
            "M:8527-9207",
            "chrM:8527-9207",
            # UCSC writes M; Ensembl and the 1000G-style references write MT.
            "MT:8527-9207",
            "chrMT:8527-9207",
            "mt:8527-9207",
        ],
    )
    def test_mitochondria_are_found_under_either_spelling(self, token):
        assert search(token) == ["MT-ATP6"]

    def test_a_database_storing_the_prefix_is_matched_by_a_bare_query(self):
        """Coordinate columns may or may not carry 'chr'; both must work."""
        prefixed = FRAME.with_columns(pl.lit("chr22").alias("chrom"))
        assert search("22:50674408-50733212", prefixed) == ["SHANK3"]
        assert search("chr22:50674408-50733212", prefixed) == ["SHANK3"]

    @pytest.mark.parametrize(
        "token",
        [
            "22:50674408..50733212",
            "22:abc-def",
            "22:-50733212",
        ],
    )
    def test_a_malformed_locus_matches_nothing_rather_than_everything(self, token):
        """An unparseable token is dropped.

        It must not fall through to a symbol match, and above all must not
        return the whole frame -- an unfiltered table reads as "no search ran".
        """
        assert search(token) == []

    def test_a_single_position_is_a_one_base_span(self):
        """A variant's locus, as genetics-viz links it."""
        assert search("22:50700000") == ["SHANK3"]
        assert search("chrX:154021573") == ["MECP2"]
        assert search("22:1") == []

    def test_the_search_is_an_overlap_not_an_exact_span(self):
        assert search("22:50700000-50700001") == ["SHANK3"]

    def test_a_span_outside_every_gene_matches_nothing(self):
        assert search("22:1-2") == []


class TestThousandsSeparators:
    """Genome browsers print separators, so pasted coordinates carry them."""

    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            ("22:50,674,408-50,733,212", ["SHANK3"]),
            ("chr22:50,674,408-50,733,212", ["SHANK3"]),
            ("chrM:8,527-9,207", ["MT-ATP6"]),
        ],
    )
    def test_a_separated_locus_resolves_to_its_gene(self, token, expected):
        assert search(token) == expected

    def test_it_no_longer_reads_the_fragments_as_gene_ids(self):
        """The regression this guards.

        Splitting on commas first turned ``22:50,674,408-50,733,212`` into
        ``22:50`` plus ``674``, ``733`` and ``212``; those were read as NCBI
        gene IDs, and the real database answered with ALAS2 and C8G -- genes on
        X and 9. The decoys below stand in for them.
        """
        decoys = pl.DataFrame(
            {
                "key": ["674", "733", "212"],
                "sym": ["DECOY1", "DECOY2", "DECOY3"],
                "chrom": ["9", "9", "9"],
                "start": [1, 2, 3],
                "end": [10, 20, 30],
            },
        )
        assert search("22:50,674,408-50,733,212", decoys, ROLES_WITH_NCBI) == []

    def test_stripping_does_not_swallow_the_following_token(self):
        assert search("22:50674408-50733212,MECP2") == ["SHANK3", "MECP2"]

    def test_a_symbol_followed_by_a_number_still_splits(self):
        """The strip must not reach across a boundary just because digits
        happen to sit either side of the comma."""
        assert search("MT-ATP6,2", roles=ROLES_WITH_NCBI) == ["MECP2", "MT-ATP6"]


class TestOtherTokens:
    def test_a_plain_symbol_list_is_unaffected(self):
        assert search("SHANK3, MECP2") == ["SHANK3", "MECP2"]

    def test_an_hgnc_id_is_not_claimed_by_the_locus_branch(self):
        """It carries a colon, so the order of the branches matters."""
        roles = Roles(
            primary_key="key",
            symbol="sym",
            hgnc="key",
            assemblies=ROLES.assemblies,
        )
        assert search("HGNC:2", roles=roles) == ["MECP2"]


#: Text pasted into the search box, and the tokens it must give. Shared by the
#: Python splitter and the browser's, which must agree on every one.
PASTED = [
    ("SHANK3, MECP2;CHD8\tSCN2A\n", ["SHANK3", "MECP2", "CHD8", "SCN2A"]),
    ("['SHANK3', 'CHD8', 'SCN2A']", ["SHANK3", "CHD8", "SCN2A"]),
    ('["SHANK3","CHD8"]', ["SHANK3", "CHD8"]),
    ("('SHANK3',)", ["SHANK3"]),
    ("{'SHANK3', 'CHD8'}", ["SHANK3", "CHD8"]),
    ("set(['SHANK3'])", ["SHANK3"]),
    ('c("SHANK3", "CHD8")', ["SHANK3", "CHD8"]),
    ("c('SHANK3','CHD8')", ["SHANK3", "CHD8"]),
    ('list("SHANK3")', ["SHANK3"]),
    ("genes = ['SHANK3',\n         'CHD8']", ["SHANK3", "CHD8"]),
    ('my.genes <- c("SHANK3", "CHD8")', ["SHANK3", "CHD8"]),
    ("[1234, 5678]", ["1234", "5678"]),
    ("`SHANK3`", ["SHANK3"]),
    # What a list's wrappers must not take with them.
    ("C3 C4A HLA-A", ["C3", "C4A", "HLA-A"]),
    ("['chr22:50,674,408-50,733,212', 'MECP2']", ["chr22:50674408-50733212", "MECP2"]),
    ("['HGNC:2', 'ENSG00000251322.5']", ["HGNC:2", "ENSG00000251322.5"]),
    ("genes MECP2", ["genes", "MECP2"]),
    (" [ ] , ", []),
]


class TestPastedCodeLists:
    """A gene list copied out of Python or R code reads as its genes.

    The brackets, ``c(`` and quotes used to stick to the first and last tokens:
    ``['SHANK3', 'CHD8', 'SCN2A']`` searched for ``[SHANK3`` and ``SCN2A]``, and
    both genes silently went missing from the results.
    """

    @pytest.mark.parametrize(("text", "expected"), PASTED)
    def test_split(self, text, expected):
        assert split_tokens(text) == expected

    @pytest.mark.parametrize(
        "text",
        ["['SHANK3', 'MECP2']", 'c("SHANK3", "MECP2")', "genes <- c('SHANK3','MECP2')"],
    )
    def test_both_ends_of_the_list_are_found(self, text):
        assert search(text) == ["SHANK3", "MECP2"]

    def test_a_search_link_carrying_a_list(self):
        link = parse_search_link("['SHANK3','MECP2']", None, ["GRCh38"])
        assert link is not None
        assert link.tokens == ("SHANK3", "MECP2")

    @pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
    def test_the_browser_splits_the_same_way(self):
        """``splitTokens`` in token_editor.js, run in node, on the same cases."""
        js = (
            Path(search_module.__file__).parent.parent / "ui" / "js" / "token_editor.js"
        )
        harness = """
            const vm = require('vm');
            const fs = require('fs');
            const window = {_tokenEditorConfig: {}};
            const document = {readyState: 'complete', getElementById: () => null};
            vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'),
                               {window, document, setTimeout, setInterval});
            const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
            console.log(JSON.stringify(cases.map(window.splitSearchTokens)));
        """
        result = subprocess.run(  # noqa: S603
            ["node", "-e", harness, str(js)],  # noqa: S607
            input=json.dumps([text for text, _ in PASTED]),
            capture_output=True,
            text=True,
            check=True,
        )
        assert json.loads(result.stdout) == [expected for _, expected in PASTED]


class TestMissingBooleanColumns:
    """A requested gene list the frame lacks is "no gene", never "no filter".

    A user list deleted in another tab leaves its column id in a selection
    while the column is gone. Skipping it returned every row -- the same
    silent failure the token guard exists for.
    """

    FLAGGED = FRAME.with_columns(pl.Series("flag", [True, False, True]))

    def search(self, cols: list[str], merge: str) -> list[str]:
        return filter_dataframe(
            self.FLAGGED,
            "",
            exact_match=True,
            selected_bool_cols=cols,
            results_merge="union",
            annotations_merge=merge,
            selected_genome="GRCh38",
            roles=ROLES,
        )["key"].to_list()

    def test_only_missing_columns_match_nothing(self):
        assert self.search(["mylist:gone"], "union") == []

    def test_a_missing_column_empties_an_intersection(self):
        assert self.search(["flag", "mylist:gone"], "intersection") == []

    def test_a_missing_column_does_not_narrow_a_union(self):
        assert self.search(["flag", "mylist:gone"], "union") == ["1", "3"]


class TestSymbolResolver:
    """Withdrawn and alias symbols, through the gene index's resolver."""

    @staticmethod
    def resolver(symbols: Sequence[str]) -> list[str]:
        renamed = {"MECP2-OLD": "2", "SHANK3": "1"}
        return [renamed[s] for s in symbols if s in renamed]

    def run(self, text: str, *, exact: bool = True) -> list[str]:
        return filter_dataframe(
            FRAME,
            text,
            exact_match=exact,
            selected_bool_cols=[],
            results_merge="union",
            annotations_merge="union",
            selected_genome="GRCh38",
            roles=ROLES,
            symbol_resolver=self.resolver,
        )["sym"].to_list()

    def test_an_old_symbol_finds_the_current_gene(self):
        assert self.run("mecp2-old") == ["MECP2"]
        assert self.run("MECP2-OLD, SHANK3") == ["SHANK3", "MECP2"]

    def test_without_a_resolver_it_does_not(self):
        assert search("MECP2-OLD") == []

    def test_substring_mode_is_left_alone(self):
        assert self.run("MECP2-OLD", exact=False) == []


class TestSearchLinks:
    def test_parse(self):
        link = parse_search_link(
            " SHANK3, chr22:50,674,408-50,733,212;MECP2 SHANK3 ",
            "grch38",
            ["GRCh37", "GRCh38"],
        )
        assert link is not None
        assert link.tokens == ("SHANK3", "chr22:50674408-50733212", "MECP2")
        assert link.text == "SHANK3,chr22:50674408-50733212,MECP2"
        assert link.genome == "GRCh38"

    def test_nothing_to_search(self):
        assert parse_search_link(None, None, ["GRCh38"]) is None
        assert parse_search_link(" , ", None, ["GRCh38"]) is None

    def test_unknown_assembly_falls_back(self):
        link = parse_search_link("SHANK3", "hg19", ["GRCh38"])
        assert link is not None
        assert link.genome is None

    def test_too_many_tokens_are_capped(self):
        text = ",".join(f"G{i}" for i in range(MAX_LINK_TOKENS + 7))
        link = parse_search_link(text, None, [])
        assert link is not None
        assert len(link.tokens) == MAX_LINK_TOKENS
        assert link.dropped == 7
        assert "7 entries beyond" in link_note(link, 0, [])

    def test_note_names_what_was_not_found(self):
        link = parse_search_link("SHANK3,NOPE,22:1-2,MECP2-OLD", None, ["GRCh38"])
        assert link is not None
        missing = unmatched_tokens(
            FRAME,
            link.tokens,
            selected_genome="GRCh38",
            roles=ROLES,
            symbol_resolver=TestSymbolResolver.resolver,
        )
        assert missing == ["NOPE", "22:1-2"]
        note = link_note(link, 2, missing)
        assert note == "from link: 4 entries → 2 gene(s); not found: NOPE, 22:1-2"


class TestResolverOverTheGeneIndex:
    """The real resolver, as the page builds it, over the synthetic tree."""

    def test_a_previous_symbol_filters_to_the_gene(self, resources, hgnc_file):
        config = load_config(resources.root)
        index = GeneIndex.from_frame(config.df, config.roles).with_hgnc(hgnc_file)
        found = filter_dataframe(
            config.df,
            "KIAA0002",
            exact_match=True,
            selected_bool_cols=[],
            results_merge="union",
            annotations_merge="union",
            selected_genome="",
            roles=config.roles,
            symbol_resolver=symbol_resolver(index),
        )
        assert found[config.roles.symbol].to_list() == ["GENE02"]
        assert symbol_resolver(None) is None
