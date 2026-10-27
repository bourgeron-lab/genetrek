"""Main NiceGUI page composition."""

from __future__ import annotations

import contextlib
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl
from nicegui import ui

from genetrek.services.lookup import symbol_resolver
from genetrek.services.search import parse_search_link
from genetrek.ui.components.column_selector import create_column_selector
from genetrek.ui.components.compare_view import CompareView
from genetrek.ui.components.gene_table import GeneTable
from genetrek.ui.components.header import create_header
from genetrek.ui.components.my_lists import MyLists
from genetrek.ui.components.search_drawer import create_search_drawer
from genetrek.ui.components.stats_drawer import create_stats_drawer
from genetrek.ui.markdown import MARKDOWN_EXTRAS
from genetrek.ui.styles import MAIN_CSS

if TYPE_CHECKING:
    from genetrek.config.loader import AppConfig
    from genetrek.db.gene_index import GeneIndex


#: Browser scripts, read per page load like the components' own.
_JS_DIR = Path(__file__).parent / "js"


def register_main_page(config: AppConfig, gene_index: GeneIndex | None = None) -> None:
    """Register the main UI page with NiceGUI.

    ``gene_index`` resolves the genes a visitor pastes or imports into a list;
    without one those dialogs report resolution as unavailable.
    """
    # Every key in the database, once per process: a list's genes are checked
    # against it to say how many the current release no longer has.
    key = config.roles.primary_key
    database_keys = (
        frozenset(config.df[key].cast(pl.String).drop_nulls().to_list())
        if key is not None
        else frozenset()
    )

    resolve_symbols = symbol_resolver(gene_index)

    @ui.page("/")
    def main_page(
        gene: str | None = None,
        search: str | None = None,
        genome: str | None = None,
    ) -> None:
        """The table page.

        Args:
            gene: Optional ``?gene=`` from the URL -- a symbol or a primary key
                whose dialog opens on load, so a gene view can be shared.
                NiceGUI registers this function as a FastAPI route, so the
                parameter is populated from the query string.
            search: Optional ``?search=`` -- genes (any identifier) and loci
                (``chr:start-end`` or ``chr:pos``), comma-separated. The table
                opens filtered to them, and the drawer shows the search. The
                same works from the fragment (``#search=``), which keeps the
                query out of server logs; see ``ui/js/search_link.js``.
            genome: Optional ``?genome=`` -- the assembly loci are read in.
        """
        ui.page_title("GeneTrek")

        create_header()

        ui.add_css(MAIN_CSS)

        # Shared mutable state
        state: dict[str, Any] = {
            "selected_columns": config.primary_columns.copy(),
            "current_page": 1,
            "rows_per_page": 20,
            "filtered_df": config.df,
            "on_filter_change": [],
            "on_lists_change": [],
        }

        # The visitor's lists. Built before every component that offers them,
        # because it seeds `state["base_df"]` and the list labels they read.
        my_lists = MyLists(config, state, gene_index, database_keys)

        # Sort handler — sorts filtered_df in-place when JS emits sort-change
        def handle_sort(sorting: list[dict[str, Any]]) -> None:
            if not sorting:
                return
            sort_cols = []
            descending = []
            for s in sorting:
                col_id = s["id"]
                if col_id in state["filtered_df"].columns:
                    sort_cols.append(col_id)
                    descending.append(s.get("desc", False))
            if sort_cols:
                state["filtered_df"] = state["filtered_df"].sort(
                    sort_cols,
                    descending=descending,
                    nulls_last=True,
                )

        # Gene table (TanStack)
        gene_table = GeneTable(config, state, on_sort_change=handle_sort)

        def refresh_table() -> None:
            state["current_page"] = 1
            gene_table.update_data()
            update_gene_count()
            _notify_filter_change(state)
            ui.run_javascript(
                "if (window._geneTableInstance)"
                " window._geneTableInstance.resetToPage(1);",
            )

        # Column selector
        def on_column_change() -> None:
            state["current_page"] = 1
            gene_table.update_data()
            ui.run_javascript(
                "if (window._geneTableInstance)"
                " window._geneTableInstance.resetToPage(1);",
            )

        column_dialog = create_column_selector(config, state, on_column_change)

        def on_lists_change() -> None:
            # `filtered_df` gained or lost a list column; its rows did not
            # change, so the page stays where it is.
            gene_table.update_data()
            update_gene_count()

        state["on_lists_change"].append(on_lists_change)

        # Search drawer
        create_search_drawer(config, state, refresh_table, resolve_symbols)

        # Stats drawer
        stats_drawer = create_stats_drawer(config, state)

        # Drawer toggle JS (needs IDs from both drawers)
        _inject_drawer_toggle_js(state)

        # Main content area
        with ui.column().classes("w-full").style("padding: 10px 26px 26px 26px;"):
            # Site description
            with ui.card().classes("w-full mb-4").style("background-color: #f0f0f0;"):
                ui.markdown(
                    config.site_description,
                    extras=MARKDOWN_EXTRAS,
                ).classes("text-sm mb-2")

                with ui.row().classes("items-center gap-2"):
                    db_format_selector = (
                        ui.select(
                            ["tsv", "parquet"],
                            value="tsv",
                            label="Format",
                        )
                        .props("dense outlined")
                        .style("min-width: 100px;")
                    )

                    async def download_database() -> None:
                        fmt = db_format_selector.value
                        ui.download(f"/api/download/database?file_format={fmt}")

                    ui.button(
                        "Download NDD database",
                        icon="download",
                        on_click=download_database,
                    ).style("background-color: #38ada9; color: white;")

            # Table | Compare. Visibility is toggled rather than using
            # `ui.tab_panels`: Quasar unmounts an inactive panel, which would
            # destroy the TanStack container and `window._geneTableInstance`.
            with (
                ui.tabs()
                .props("dense align=left no-caps active-color=teal")
                .classes(
                    "w-full",
                )
                .style("color: #2c5282;") as view_tabs
            ):
                ui.tab("table", label="Table", icon="table_rows")
                ui.tab("compare", label="Compare gene sets", icon="join_inner")
            view_tabs.set_value("table")
            table_view = ui.column().classes("w-full gap-2")
            compare_holder = ui.column().classes("w-full")
            compare_holder.set_visibility(False)

            def show_region(genes: frozenset[str], label: str) -> None:
                key = config.roles.primary_key
                if key is None:
                    return
                state["filtered_df"] = state["base_df"].filter(
                    pl.col(key).cast(pl.String).is_in(sorted(genes)),
                )
                state["results_note"] = f"from Compare: {label}"
                refresh_table()
                view_tabs.set_value("table")

            with compare_holder:
                compare_view = CompareView(config, state, my_lists, show_region)

            def switch_view(value: str) -> None:
                table_view.set_visibility(value == "table")
                compare_holder.set_visibility(value == "compare")
                compare_view.set_visible(visible=value == "compare")

            view_tabs.on_value_change(lambda e: switch_view(e.value))

            def open_compare(set_id: str) -> None:
                """Compare the current results with one set, from the drawer.

                The drawer closes: it overlays the main area on a narrow window,
                so the comparison would open underneath it.
                """
                stats_drawer.hide()
                view_tabs.set_value("compare")
                compare_view.open_with(["results", set_id])

            state["open_compare"] = open_compare

            with table_view:
                # Controls row
                with ui.row().classes("items-center w-full justify-between"):
                    with ui.row().classes("items-center gap-2"):
                        ui.button(
                            "Select Columns",
                            icon="view_column",
                            on_click=column_dialog.open,
                        ).style("color: #38ada9;")
                        if my_lists.enabled:
                            ui.button(
                                "My lists",
                                icon="bookmarks",
                                on_click=my_lists.open_manager,
                            ).style("color: #38ada9;").tooltip(
                                "Gene lists saved in this browser only",
                            )

                    with ui.row().classes("items-center gap-2"):
                        if my_lists.enabled:
                            ui.button(
                                "Save as list",
                                icon="bookmark_add",
                                on_click=my_lists.open_save,
                            ).props("outline").style("color: #38ada9;").tooltip(
                                "Keep the current results as a gene list, in this "
                                "browser only",
                            )

                        results_format_selector = (
                            ui.select(
                                ["tsv", "parquet"],
                                value="tsv",
                                label="Format",
                            )
                            .props("dense outlined")
                            .style("min-width: 100px;")
                        )

                        async def download_current_results() -> None:
                            fmt = results_format_selector.value
                            filtered_df = _readable_list_columns(
                                state["filtered_df"],
                                state["list_labels"],
                            )
                            today = datetime.now(tz=UTC).strftime("%Y-%m-%d")

                            if fmt == "tsv":
                                buffer = io.BytesIO()
                                filtered_df.write_csv(buffer, separator="\t")
                                content = buffer.getvalue()
                                ui.download(
                                    content,
                                    f"genetrek-data-{today}.tsv",
                                    "text/tab-separated-values",
                                )
                            elif fmt == "parquet":
                                buffer = io.BytesIO()
                                filtered_df.write_parquet(buffer)
                                content = buffer.getvalue()
                                ui.download(
                                    content,
                                    f"genetrek-data-{today}.parquet",
                                    "application/octet-stream",
                                )

                        ui.button(
                            "Download current results",
                            icon="download",
                            on_click=download_current_results,
                        ).style("background-color: #38ada9; color: white;")

                # Gene count
                gene_count_label = (
                    ui.label(f"{len(config.df)} gene(s) found")
                    .classes("text-xl font-bold mt-2")
                    .style("color: #2c5282;")
                )

                def update_gene_count() -> None:
                    count = len(state["filtered_df"])
                    note = state.get("results_note") or ""
                    gene_count_label.set_text(
                        f"{count} gene(s) found" + (f" — {note}" if note else ""),
                    )

                # Table container (pagination is rendered by JS inside the container)
                table_pagination_container = ui.column().classes(
                    "items-center w-full overflow-x-hidden",
                )
                gene_table.container.move(table_pagination_container)

        # Initial data push (deferred to ensure JS is ready)
        ui.timer(0.5, lambda: gene_table.update_data(), once=True)

        # A shared link opens its gene once the page exists. Deferred for the
        # same reason as the push above, and after it so the table is populated
        # behind the dialog rather than appearing under it.
        def run_search_link(text: str | None, assembly: str | None) -> None:
            link = parse_search_link(text, assembly, list(config.roles.assemblies))
            if link is None:
                return
            state["show_search_link"](link)
            view_tabs.set_value("table")

        if search:
            ui.timer(0.55, lambda: run_search_link(search, genome), once=True)
        ui.on(
            "searchLink",
            lambda e: run_search_link(e.args.get("search"), e.args.get("genome")),
        )
        ui.add_body_html(
            f"<script>{(_JS_DIR / 'search_link.js').read_text()}</script>",
        )

        if gene:
            ui.timer(0.6, lambda: gene_table.open_gene_info(gene), once=True)


def _readable_list_columns(
    frame: pl.DataFrame,
    labels: dict[str, str],
) -> pl.DataFrame:
    """Name the visitor's list columns by their labels for a download.

    ``mylist:<id>`` means nothing outside this tab. Labels are unique in
    practice -- they carry the creation minute -- but two lists made in the
    same minute under one name are possible, and a frame cannot hold two
    columns of one name, so a clash keeps the id as well.
    """
    names: dict[str, str] = {}
    taken = set(frame.columns)
    for column, text in labels.items():
        if column not in frame.columns:
            continue
        name = f"my list: {text}"
        if name in taken:
            name = f"{name} [{column.split(':', 1)[1]}]"
        taken.add(name)
        names[column] = name
    return frame.rename(names) if names else frame


def _notify_filter_change(state: dict[str, Any]) -> None:
    """Call all registered filter change callbacks."""
    for callback in state.get("on_filter_change", []):
        with contextlib.suppress(Exception):
            callback()


def _inject_drawer_toggle_js(state: dict[str, Any]) -> None:
    """Inject the drawer toggle positioning JavaScript."""
    left_toggle_id = state.get("_left_toggle_id", "search-panel-toggle")
    left_hidden_btn_id = state.get("_left_hidden_btn_id", "")
    right_toggle_id = state.get("_right_toggle_id", "stats-panel-toggle")
    right_hidden_btn_id = state.get("_right_hidden_btn_id", "")

    js_path = Path(__file__).parent / "js" / "drawer_toggle.js"
    js_content = js_path.read_text()

    ui.add_body_html(f"""
    <script>
    window._drawerToggleConfig = {{
        leftToggleId: '{left_toggle_id}',
        leftHiddenBtnId: '{left_hidden_btn_id}',
        rightToggleId: '{right_toggle_id}',
        rightHiddenBtnId: '{right_hidden_btn_id}'
    }};
    {js_content}
    </script>
    """)
