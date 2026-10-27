"""TanStack Table wrapper component for the gene data table."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nicegui import ui

from genetrek.schema import BADGE_RENDERED_COLUMNS, badge_key, is_scale_config
from genetrek.ui.components.column_info_dialog import (
    column_tooltip,
    create_column_info_dialog,
)
from genetrek.ui.components.gene_info_dialog import create_gene_info_dialog

if TYPE_CHECKING:
    from collections.abc import Callable

    from genetrek.config.loader import AppConfig

#: Pinned deliberately. Unpinned, this URL followed the dist-tag to table-core
#: 9, which replaced ``createTable`` with a ``tableFeatures`` API -- so the table
#: stopped rendering with ``TanStack.getCoreRowModel is not a function`` and no
#: code change on our side. ui/js/gene_table.js is written against the v8 API.
_TANSTACK_CDN = "https://cdn.jsdelivr.net/npm/@tanstack/table-core@8.21.3/+esm"


class GeneTable:
    """Wraps a client-side TanStack Table instance, bridging NiceGUI and JS.

    Pagination UI and state live in JS. Python handles data slicing and sorting.
    Communication is via NiceGUI's emitEvent (JS→Python) and ui.run_javascript
    (Python→JS).
    """

    def __init__(
        self,
        config: AppConfig,
        state: dict[str, Any],
        on_sort_change: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.on_sort_change = on_sort_change

        self.container_id = "gene-table-container"
        self._inject_scripts()
        self.container = ui.html(
            f'<div id="{self.container_id}"></div>',
            sanitize=False,
        )
        self._init_table()

        # Owned here rather than passed in: nothing outside the table opens
        # these, and the events they answer arrive on this component's bridge.
        self._column_dialog, self._show_column_info = create_column_info_dialog(
            config,
            describe_extra=lambda column: state.get(
                "describe_list_column",
                lambda _: None,
            )(column),
        )
        self._gene_dialog, self._show_gene_info = create_gene_info_dialog(config)

        # Listen for events from JS pagination/sorting controls. The names look
        # mismatched against the camelCase the JS emits, but ``Element.on`` runs
        # the type through ``helpers.event_type_to_camel_case`` first, so these
        # register as ``pageChange``/``sortChange`` and do match. ``columnInfo``
        # has no dash, so it is already invariant under that conversion.
        ui.on("page-change", self._handle_page_change)
        ui.on("sort-change", self._handle_sort_change)
        ui.on("columnInfo", self._handle_column_info)
        ui.on("geneInfo", self._handle_gene_info)

    def _inject_scripts(self) -> None:
        """Inject the TanStack CDN import and the gene_table.js class."""
        gene_table_js = (
            Path(__file__).parent.parent / "js" / "gene_table.js"
        ).read_text()

        # Use a regular <script> to keep definitions in global scope.
        # TanStack is loaded via dynamic import() which returns a promise.
        ui.add_head_html(f"""
        <script>
        (function() {{
            // Load TanStack core via dynamic import, then define GeneTrekTable
            import('{_TANSTACK_CDN}').then(function(mod) {{
                window.TanStackTable = {{
                    createTable: mod.createTable,
                    getCoreRowModel: mod.getCoreRowModel,
                }};

                // Gene table class (global scope)
                {gene_table_js}
            }}).catch(function(err) {{
                console.error('[GeneTrek] Failed to load TanStack:', err);
            }});
        }})();
        </script>
        """)

    def _build_column_defs(self, columns: list[str]) -> list[dict[str, Any]]:
        """Build column definitions for the JS table, in the given order."""
        col_defs = []
        list_labels: dict[str, str] = self.state.get("list_labels", {})
        for col in columns:
            if col in list_labels:
                # A visitor's own list: no metadata, always a Boolean.
                col_defs.append(
                    {
                        "id": col,
                        "header": list_labels[col],
                        "cellType": "boolean",
                        "badgeConfig": None,
                        "tooltip": "Your list, stored in this browser only",
                        "markNull": False,
                    },
                )
                continue
            col_config = self.config.display_config.get(col, {})
            fullname = col_config.get("fullname", col)

            # Determine cell type. A versioned column is configured under its
            # alias (inheritance_v7 under inheritance), so look it up by that.
            meta = self.config.fields.get(col)
            key = badge_key(
                col,
                meta.aliases if meta else (),
                self.config.badges_config,
            )
            configured = self.config.badges_config.get(key)
            if is_scale_config(configured):
                # Driven entirely from badges.yaml -- unlike a badge, a scale
                # needs no entry in BADGE_RENDERED_COLUMNS to reach the client.
                cell_type = "scale"
                badge_config = configured
            elif key in BADGE_RENDERED_COLUMNS:
                cell_type = "badge"
                badge_config = configured
            elif col in self.config.boolean_columns:
                cell_type = "boolean"
                badge_config = None
            else:
                cell_type = "text"
                badge_config = None

            col_defs.append(
                {
                    "id": col,
                    "header": fullname,
                    "cellType": cell_type,
                    "badgeConfig": badge_config,
                    "tooltip": column_tooltip(col_config),
                    # A gene list's null, or any null in a column that declares
                    # which genes it measured, is "not measured" -- not "no".
                    "markNull": cell_type == "boolean"
                    or bool(col_config.get("universe")),
                },
            )
        return col_defs

    def _init_table(self) -> None:
        """Initialize the JS table instance after DOM is ready."""
        col_defs_json = json.dumps(
            self._build_column_defs(self.state["selected_columns"]),
        )
        # The table needs to know which column is the key and which the symbol,
        # so a row click can name the gene. It reads neither by literal.
        roles_json = json.dumps(
            {
                "primaryKey": self.config.roles.primary_key,
                "symbolColumn": self.config.roles.symbol,
            },
        )

        ui.timer(
            0.1,
            lambda: ui.run_javascript(f"""
                (function tryInit(attempt) {{
                    var container = document.getElementById('{self.container_id}');
                    if (!window.TanStackTable || !window.GeneTrekTable || !container) {{
                        if (attempt < 50) {{
                            setTimeout(function() {{ tryInit(attempt + 1); }}, 200);
                        }} else {{
                            console.error('[GeneTrek] Table init failed after 50 attempts');
                        }}
                        return;
                    }}
                    if (window._geneTableInstance) return;
                    window._geneTableInstance = new GeneTrekTable(
                        '{self.container_id}',
                        {{
                            columnDefs: {col_defs_json},
                            roles: {roles_json},
                        }}
                    );
                }})(1);
            """),
            once=True,
        )

    def update_data(self) -> None:
        """Push current page data + totalRows to the client-side table."""
        self._push_page_data()

    def _push_page_data(self) -> None:
        """Slice filtered_df and push current page data + totalRows to JS."""
        filtered_df = self.state["filtered_df"]
        current_page = self.state["current_page"]
        rows_per_page = self.state["rows_per_page"]
        selected_columns = self.state["selected_columns"]

        start_idx = (current_page - 1) * rows_per_page
        total_rows = len(filtered_df)

        # Select all common columns (needed for external link URL placeholders),
        # plus whichever of the visitor's lists are shown.
        list_columns = [
            col
            for col in selected_columns
            if col in self.state.get("list_labels", {}) and col in filtered_df.columns
        ]
        page_data = (
            filtered_df.select([*self.config.common_columns, *list_columns])
            .slice(start_idx, rows_per_page)
            .to_dicts()
        )
        data_json = json.dumps(page_data, default=str)
        col_defs_json = json.dumps(self._build_column_defs(selected_columns))

        ui.run_javascript(f"""
            (function pushData() {{
                if (!window._geneTableInstance) {{
                    setTimeout(pushData, 100);
                    return;
                }}
                window._geneTableInstance.updateColumnsAndData(
                    {col_defs_json}, {data_json}, {total_rows}
                );
            }})();
        """)

    def _handle_page_change(self, e: Any) -> None:  # noqa: ANN401
        """Handle page-change event from JS pagination controls."""
        data = e.args
        self.state["current_page"] = data["page"]
        self.state["rows_per_page"] = data["rowsPerPage"]
        self._push_page_data()

    def _handle_column_info(self, e: Any) -> None:  # noqa: ANN401
        """Open the column dialog for a clicked header label."""
        column = e.args.get("column")
        if column:
            self._show_column_info(column)

    def open_gene_info(self, query: str) -> None:
        """Open the gene dialog for a primary key or a symbol.

        Public because a ``?gene=`` link is honoured by the page, which does not
        own the dialog. An unresolvable value opens nothing.
        """
        self._show_gene_info(query)

    def _handle_gene_info(self, e: Any) -> None:  # noqa: ANN401
        """Open the gene dialog for a clicked symbol or row info button."""
        gene = e.args.get("gene")
        if gene:
            self.open_gene_info(gene)

    def _handle_sort_change(self, e: Any) -> None:  # noqa: ANN401
        """Handle sort-change event from JS column header clicks."""
        data = e.args
        sorting = data.get("sorting", [])
        if self.on_sort_change:
            self.on_sort_change(sorting)
        # After sorting callback updates filtered_df, reset to page 1
        self.state["current_page"] = 1
        self._push_page_data()
        ui.run_javascript(
            "if (window._geneTableInstance) window._geneTableInstance.resetToPage(1);",
        )
