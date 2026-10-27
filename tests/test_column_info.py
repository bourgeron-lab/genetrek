"""Tests for the column-info dialog's markdown builder.

The dialog itself needs a browser; this is the part with logic in it. What it
guards is mostly formatting that has a wrong answer: a bare DOI has to become a
followable link, and a description naming columns must not be read as emphasis.
"""

from __future__ import annotations

import markdown2
import pytest

from genetrek.ui.components.column_info_dialog import (
    column_info_markdown,
    column_tooltip,
)
from genetrek.ui.markdown import MARKDOWN_EXTRAS


def render(markdown: str) -> str:
    """Render exactly as the dialog does, so extras are covered too."""
    return markdown2.markdown(markdown, extras=MARKDOWN_EXTRAS)


class TestDescription:
    def test_the_description_leads(self):
        body = column_info_markdown("hgnc_symbol", {"description": "HGNC symbol"})
        assert body.startswith("HGNC symbol")

    def test_a_missing_description_says_so_rather_than_rendering_blank(self):
        body = column_info_markdown("mystery", {})
        assert "No description recorded" in body

    def test_underscored_identifiers_survive_rendering(self):
        """markdown2 reads ``_`` as emphasis by default; descriptions use it."""
        body = column_info_markdown(
            "gnomad_flags",
            {"description": "flags: low_exome_coverage, no_exp_lof"},
        )
        assert "low_exome_coverage" in render(body)
        assert "<em>" not in render(body)

    def test_deliberate_emphasis_still_works(self):
        body = column_info_markdown("x", {"description": "This is *not* optional."})
        assert "<em>not</em>" in render(body)


class TestFacts:
    def test_a_bare_doi_becomes_a_followable_link(self):
        body = column_info_markdown("x", {"doi": "10.1038/s41588-023-01451-6"})
        assert "(https://doi.org/10.1038/s41588-023-01451-6)" in body
        assert 'href="https://doi.org/10.1038/s41588-023-01451-6"' in render(body)

    def test_a_doi_already_a_url_is_not_double_prefixed(self):
        body = column_info_markdown("x", {"doi": "https://doi.org/10.1038/abc"})
        assert "https://doi.org/https://" not in body

    def test_links_open_outside_the_single_page(self):
        """A same-tab navigation would discard the table's filter/sort state."""
        body = column_info_markdown("x", {"doi": "10.1038/abc"})
        assert 'target="_blank"' in render(body)

    def test_dates_are_rendered_readably(self):
        body = column_info_markdown("x", {"update": "20260828"})
        assert "2026-08-28" in body

    def test_an_unparseable_date_is_shown_rather_than_dropped(self):
        body = column_info_markdown("x", {"update": "spring 2026"})
        assert "spring 2026" in body

    @pytest.mark.parametrize("empty", ["", None])
    def test_empty_facts_are_omitted_rather_than_shown_blank(self, empty):
        body = column_info_markdown("x", {"source": "HGNC", "doi": empty})
        assert "**Source:** HGNC" in body
        assert "DOI" not in body

    def test_an_assembly_qualifies_the_value_type(self):
        body = column_info_markdown(
            "start_grch38",
            {"value_type": "integer", "assembly": "GRCh38"},
        )
        assert "**Type:** integer (GRCh38)" in body

    def test_a_derived_column_is_described_as_its_rule(self):
        body = column_info_markdown(
            "hc_ndd",
            {"derived_op": "union", "derived_from": ["a", "b"]},
        )
        assert "**Derived:** union of `a`, `b`" in body

    def test_an_external_derivation_names_its_parent(self):
        body = column_info_markdown(
            "brain_enriched",
            {"derived_op": "external", "derived_from": ["brain_ratio"]},
        )
        assert "computed from `brain_ratio` outside GeneTrek" in body

    def test_the_universe_and_its_size_are_reported(self):
        body = column_info_markdown(
            "flag",
            {"universe": "Proteins quantified in brain"},
            n_measured=8_911,
            n_rows=45_083,
        )
        assert "**Universe:** Proteins quantified in brain" in body
        assert "8,911 of 45,083 genes measured" in body

    def test_a_partial_column_without_a_universe_still_says_so(self):
        body = column_info_markdown("flag", {}, n_measured=10, n_rows=20)
        assert "**Universe:** 10 of 20 genes measured" in body

    def test_full_coverage_says_nothing(self):
        body = column_info_markdown("flag", {}, n_measured=20, n_rows=20)
        assert "Universe" not in body

    def test_a_derivation_missing_its_sources_is_not_half_reported(self):
        body = column_info_markdown("x", {"derived_op": "union", "derived_from": []})
        assert "Derived" not in body


def test_the_column_name_is_always_reported():
    """The header shows the fullname, so the parquet name is worth stating."""
    body = column_info_markdown("shet_mean", {"fullname": "sHet"})
    assert "`shet_mean`" in body


class TestGroup:
    def test_a_category_and_subcategory_render_as_a_path(self):
        body = column_info_markdown(
            "loeuf",
            {"fullname": "LOEUF", "category": "Constraint", "subcategory": "gnomAD"},
        )
        assert "**Group:** Constraint / gnomAD" in body

    def test_a_category_alone_renders_alone(self):
        body = column_info_markdown(
            "strand",
            {"fullname": "Strand", "category": "Coordinates", "subcategory": ""},
        )
        assert "**Group:** Coordinates" in body

    def test_no_category_shows_no_group(self):
        body = column_info_markdown("x", {"fullname": "X"})
        assert "Group" not in body

    def test_a_subcategory_without_a_category_is_dropped(self):
        """It has no group to sit in; `validate` warns about the metadata."""
        body = column_info_markdown(
            "x",
            {"fullname": "X", "category": "", "subcategory": "Orphan"},
        )
        assert "Group" not in body
        assert "Orphan" not in body


class TestBareUrlsBecomeLinks:
    """Provenance is written as prose, and a URL in it has to be followable.

    Rendered rather than asserted on the markdown, because what matters is the
    anchor the reader gets -- including the ``target="_blank"`` that
    ``target-blank-links`` adds, since this is a single-page app and a same-tab
    navigation would discard the table's state.
    """

    def test_a_url_in_the_source_is_rendered_as_a_link(self):
        html = render(
            column_info_markdown(
                "eagle_score",
                {
                    "fullname": "EAGLE class",
                    "source": (
                        "https://gene.sfari.org/about-gene-scoring/eagle-score/"
                        " - EAGLE score, 2026-03-22 release"
                    ),
                },
            ),
        )
        assert 'href="https://gene.sfari.org/about-gene-scoring/eagle-score/"' in html
        assert 'target="_blank"' in html
        # The prose after the URL survives, rather than being swallowed by it.
        assert "EAGLE score, 2026-03-22 release" in html

    def test_a_url_in_the_description_is_rendered_as_a_link(self):
        html = render(
            column_info_markdown(
                "x",
                {"fullname": "X", "description": "Taken from https://example.org/set"},
            ),
        )
        assert 'href="https://example.org/set"' in html

    def test_a_url_mid_sentence_keeps_its_punctuation_out_of_the_href(self):
        html = render(
            column_info_markdown(
                "x",
                {"fullname": "X", "description": "See https://example.org/page."},
            ),
        )
        assert 'href="https://example.org/page"' in html
        assert 'href="https://example.org/page."' not in html

    def test_an_existing_markdown_link_is_left_alone(self):
        """Descriptions are markdown, so some already carry a written link."""
        html = render(
            column_info_markdown(
                "x",
                {"fullname": "X", "description": "See [the spec](https://example.org)"},
            ),
        )
        assert html.count("<a ") == 1
        assert ">the spec<" in html

    def test_text_with_no_url_is_untouched(self):
        body = column_info_markdown(
            "x",
            {"fullname": "X", "source": "GeneTrek curation"},
        )
        assert "**Source:** GeneTrek curation" in body

    def test_a_doi_still_renders_as_its_own_link(self):
        """`_format_doi` owns the DOI; linkifying must not double-wrap it."""
        html = render(
            column_info_markdown(
                "x",
                {"fullname": "X", "doi": "10.1038/s41586-024-07556-0"},
            ),
        )
        assert 'href="https://doi.org/10.1038/s41586-024-07556-0"' in html
        assert html.count("<a ") == 1


class TestTooltipStaysPlainText:
    """The tooltip is a native ``title``, which renders nothing.

    It shares its fields with the dialog but not its formatting: markdown in a
    ``title`` shows up as literal ``[url](url)``, so the linkifying that makes
    the dialog useful would make this worse. Pinned because the two live side
    by side and the wrong one is easy to reach for.
    """

    SOURCE = "PanelApp Australia - https://panelapp-aus.org/panels/51/ v0.248"

    def test_a_url_is_left_bare(self):
        text = column_tooltip({"fullname": "X", "source": self.SOURCE})
        assert self.SOURCE in text
        assert "](" not in text

    def test_it_leads_with_the_label_and_lists_one_fact_per_line(self):
        text = column_tooltip(
            {
                "fullname": "EAGLE class",
                "description": "Phenotypic specificity to autism.",
                "update": "20260322",
                "source": "SFARI",
                "doi": "10.1038/s41576-020-0231-2",
            },
        )
        assert text.splitlines() == [
            "EAGLE class",
            "Description: Phenotypic specificity to autism.",
            "Updated: 2026-03-22",
            "Source: SFARI",
            "DOI: 10.1038/s41576-020-0231-2",
        ]

    def test_empty_fields_are_dropped_rather_than_shown_blank(self):
        assert column_tooltip({"fullname": "X"}) == "X"
