"""The prose pages: one registration per entry in ``genetrek.content``.

Replaces the single ``/documentation`` page. Every page is the same shape --
header, a width-capped card, rendered markdown -- so the only thing that varies
is which file it reads and, for the changelog, a computed section appended
below the prose.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nicegui import ui

from genetrek.content import CONTENT_PAGES
from genetrek.services.column_reference import (
    column_reference_rows,
    column_reference_tsv,
)
from genetrek.services.versions import version_table_markdown
from genetrek.ui.components.header import create_header
from genetrek.ui.markdown import MARKDOWN_EXTRAS
from genetrek.ui.styles import MAIN_CSS

if TYPE_CHECKING:
    from collections.abc import Callable

    from genetrek.config.loader import AppConfig
    from genetrek.content import ContentPage


#: Slug -> a renderer for content computed rather than written, drawn below the
#: markdown. Kept here rather than on ``ContentPage`` so ``genetrek.content``
#: stays importable by ``config/`` and ``db/``, neither of which may pull in
#: NiceGUI.
def _render_version_comparison(config: AppConfig) -> None:
    """The changelog's version table, computed from the published data.

    Rendered as markdown so it is styled by the same rules as the authored
    tables above it. Omitted entirely when no gene list has a surviving older
    version, rather than shown empty.
    """
    table = version_table_markdown(config.df, config.field_aliases)
    if not table:
        ui.label(
            "No gene list currently has an older version in the table.",
        ).classes("text-sm italic")
        return
    ui.markdown(table, extras=MARKDOWN_EXTRAS).classes("text-sm")


#: Sorts by display order. Category and subcategory sort this way rather than
#: alphabetically, so sorting on either gives back the table's own grouping.
_BY_RANK = "(a, b, rowA, rowB) => rowA.rank - rowB.rank"

#: A count with thousands separators; blank where there is none to show.
_COUNT = "value => value == null ? '' : value.toLocaleString('en-US')"

_REFERENCE_COLUMNS: list[dict[str, Any]] = [
    # The filter matches the column key too, and the description cell also
    # shows the universe, so both fields are searched -- Quasar filters on
    # each column's `field`, not on whatever a slot draws.
    {
        "name": "label",
        "label": "Column",
        ":field": "row => `${row.label} ${row.key}`",
        "sortable": True,
        "align": "left",
        "style": "min-width: 180px; max-width: 220px",
    },
    {
        "name": "category",
        "label": "Category",
        "field": "category",
        "sortable": True,
        ":sort": _BY_RANK,
        "align": "left",
    },
    {
        "name": "subcategory",
        "label": "Subcategory",
        "field": "subcategory",
        "sortable": True,
        ":sort": _BY_RANK,
        "align": "left",
    },
    {
        "name": "description",
        "label": "Description",
        ":field": "row => `${row.description} ${row.universe}`",
        "align": "left",
        "style": "min-width: 320px",
    },
    {
        "name": "source",
        "label": "Source",
        "field": "source",
        "sortable": True,
        "align": "left",
        "style": "min-width: 160px; overflow-wrap: anywhere",
    },
    {
        "name": "doi",
        "label": "DOI",
        "field": "doi",
        "align": "left",
        "style": "min-width: 150px; max-width: 170px",
    },
    {
        "name": "licence",
        "label": "Licence",
        "field": "licence",
        "sortable": True,
        "align": "left",
        "style": "min-width: 110px; overflow-wrap: anywhere",
    },
    {
        "name": "updated",
        "label": "Updated",
        "field": "updated",
        "sortable": True,
        "align": "left",
        "classes": "text-no-wrap",
    },
    {
        "name": "n_genes",
        "label": "Genes",
        "field": "n_genes",
        "sortable": True,
        ":format": _COUNT,
    },
    {
        "name": "n_measured",
        "label": "Measured",
        "field": "n_measured",
        "sortable": True,
        ":format": _COUNT,
    },
]

#: The header stays in view while the rows scroll inside the card, and never
#: wraps -- `wrap-cells` would otherwise push a sort arrow onto its own line.
#: Cells align at the top, so a label sits beside the start of a long
#: description.
_REFERENCE_CSS = """
.column-reference { max-height: 75vh; }
.column-reference tbody td { vertical-align: top; }
.column-reference thead tr th {
  position: sticky; top: 0; z-index: 1; background: #fff;
  white-space: nowrap;
}
"""


def _render_column_reference(config: AppConfig) -> None:
    """Every column, hidden ones included, as one filterable table.

    Built from ``config`` on each request, so it lists what this database
    holds. Metadata is drawn as text throughout -- no ``v-html`` -- so nothing
    in a description is ever interpreted as markup.
    """
    rows = column_reference_rows(config.fields, config.categories, config.df)
    filename = (
        f"genetrek-columns-{config.release}.tsv"
        if config.release
        else "genetrek-columns.tsv"
    )

    ui.add_css(_REFERENCE_CSS)
    with ui.row().classes("w-full items-center gap-4 mt-4"):
        search = (
            ui.input(placeholder="Filter columns")
            .props("dense outlined clearable")
            .classes("w-72")
        )
        ui.label(
            f"{len(rows):,} columns · {config.df.height:,} genes",
        ).classes("text-sm text-grey-7")
        ui.space()
        ui.button(
            "Download TSV",
            icon="download",
            on_click=lambda: ui.download.content(
                column_reference_tsv(rows),
                filename,
                "text/tab-separated-values",
            ),
        ).props("flat dense no-caps")

    table = (
        ui.table(
            # Copied: NiceGUI keeps the list as this element's prop.
            columns=[dict(column) for column in _REFERENCE_COLUMNS],
            rows=rows,
            row_key="key",
            pagination=0,
        )
        .props("dense flat bordered wrap-cells")
        .classes("w-full column-reference")
    )
    search.bind_value(table, "filter")
    table.add_slot(
        "body-cell-label",
        """
        <q-td :props="props">
          <div class="font-medium">{{ props.row.label }}</div>
          <code class="text-xs text-grey-8"
                style="overflow-wrap: anywhere">{{ props.row.key }}</code>
          <div v-if="props.row.api_only">
            <q-badge color="grey-5" text-color="black" label="API only"
                     title="Not offered by the gene table; read it through the API" />
          </div>
        </q-td>
        """,
    )
    table.add_slot(
        "body-cell-description",
        """
        <q-td :props="props">
          <div>{{ props.row.description }}</div>
          <div v-if="props.row.universe" class="text-xs text-grey-7 q-mt-xs">
            Measured: {{ props.row.universe }}
          </div>
        </q-td>
        """,
    )
    table.add_slot(
        "body-cell-doi",
        """
        <q-td :props="props">
          <a v-if="props.row.doi_url" :href="props.row.doi_url" target="_blank"
             rel="noopener" class="text-primary"
             style="overflow-wrap: anywhere">{{ props.row.doi }}</a>
        </q-td>
        """,
    )


_COMPUTED_SECTIONS: dict[str, Callable[[AppConfig], None]] = {
    "changelog": _render_version_comparison,
    "columns": _render_column_reference,
}


def _register(config: AppConfig, page: ContentPage) -> None:
    """Register one prose page.

    A function rather than a loop body, and this is not stylistic:
    ``@ui.page(page.route)`` binds the route when the decorator runs, but the
    body reads ``page.slug`` when a request arrives. Inlined in a loop, all
    four routes would close over the same variable and serve four copies of the
    last page.
    """

    @ui.page(page.route)
    def content_page() -> None:
        ui.page_title(f"GeneTrek — {page.title}")

        create_header(current=page.route)

        ui.add_css(MAIN_CSS)

        # The card is width-capped rather than full-bleed: this is prose, and
        # the table's reason for spanning the viewport does not apply here. A
        # wide page carries a table of its own, which gets more room.
        max_width = 1400 if page.wide else 900
        with (
            ui.column().classes("w-full items-center").style("padding: 26px;"),
            ui.card().classes("w-full").style(f"max-width: {max_width}px;"),
        ):
            ui.markdown(
                config.documents[page.slug],
                extras=MARKDOWN_EXTRAS,
            ).classes("text-sm")
            if (computed := _COMPUTED_SECTIONS.get(page.slug)) is not None:
                computed(config)


def register_content_pages(config: AppConfig) -> None:
    """Register every declared prose page with NiceGUI."""
    for page in CONTENT_PAGES:
        _register(config, page)
