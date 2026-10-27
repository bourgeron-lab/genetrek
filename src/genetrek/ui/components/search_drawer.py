"""Left drawer: search interface with token editor and filters."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from nicegui import ui

from genetrek.services.search import (
    SearchLink,
    filter_dataframe,
    link_note,
    unmatched_tokens,
)
from genetrek.ui.components.token_editor import (
    create_search_trigger,
    create_token_editor,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from genetrek.config.loader import AppConfig
    from genetrek.services.search import SymbolResolver


def create_search_drawer(
    config: AppConfig,
    state: dict[str, Any],
    on_search: Callable[[], None],
    symbol_resolver: SymbolResolver | None = None,
) -> ui.left_drawer:
    """Create the search/filter left drawer. Returns the drawer element.

    Also sets ``state["show_search_link"]``, which runs a search that arrived
    in the URL as if it had been typed here, so the drawer shows it.
    """
    with (
        ui.left_drawer()
        .classes("text-white p-4 relative")
        .props("width=500")
        .style("background-color: #2c5282;") as drawer,
        ui.column().classes("w-full"),
    ):
        with ui.row().classes("items-center mb-4"):
            ui.icon("search").classes("text-2xl")
            ui.label("Search").classes("text-xl font-bold ml-2")

        ui.label(
            "Enter a list of NCBI IDs, Ensembl IDs, gene symbols or loci:",
        ).classes("text-sm mb-2")

        search_input = create_token_editor(config.token_colors_config)

        def perform_search() -> None:
            search_text = search_input.value
            exact_match = exact_match_toggle.value
            # The visitor's lists are Boolean columns of `base_df`, so they
            # filter exactly as database gene lists do, merged the same way.
            selected_bool_cols = [
                *(bool_column_selector.value or []),
                *(my_lists_selector.value or []),
            ]
            results_merge = results_merge_radio.value
            annotations_merge = annotations_merge_radio.value
            selected_genome = genome_version.value

            state["filtered_df"] = filter_dataframe(
                state["base_df"],
                search_text,
                exact_match=exact_match,
                selected_bool_cols=selected_bool_cols,
                results_merge=results_merge,
                annotations_merge=annotations_merge,
                selected_genome=selected_genome,
                roles=config.roles,
                symbol_resolver=symbol_resolver,
            )
            state["current_page"] = 1
            state["results_note"] = ""
            on_search()

        def show_search_link(link: SearchLink) -> None:
            """Fill the drawer from a link and run it, with a note on the result."""
            search_input.value = link.text
            if link.genome is not None:
                genome_version.value = link.genome
            exact_match_toggle.value = True
            bool_column_selector.value = []
            my_lists_selector.value = []
            results_merge_radio.value = "union"
            # The badges are drawn by the token editor, in the browser.
            ui.run_javascript(
                f"if (window.setSearchTokens) window.setSearchTokens({json.dumps(link.text)});",
            )
            perform_search()
            missing = unmatched_tokens(
                state["base_df"],
                link.tokens,
                selected_genome=genome_version.value,
                roles=config.roles,
                symbol_resolver=symbol_resolver,
            )
            state["results_note"] = link_note(link, len(state["filtered_df"]), missing)
            on_search()

        def clear_search() -> None:
            search_input.value = ""
            bool_column_selector.value = []
            my_lists_selector.value = []
            state["filtered_df"] = state["base_df"]
            state["current_page"] = 1
            state["results_note"] = ""
            on_search()

        # Search options
        with ui.row().classes("gap-4 mt-2 items-center"):
            exact_match_toggle = (
                ui.switch("Only exact matching for gene symbols")
                .props("color=teal")
                .style("color: white;")
            )
            exact_match_toggle.value = True

        # Genome version
        with ui.element("div").style(
            "border: 1px solid white; border-radius: 8px; padding: 8px 16px; "
            "display: flex; align-items: center; gap: 8px; margin-top: 8px;",
        ):
            ui.label("Genome version:").classes("text-sm")
            # Assemblies come from the metadata, so a database using
            # GRCh37/GRCh38 column names offers those instead of hg19/hg38.
            assemblies = sorted(config.roles.assemblies)
            genome_version = (
                ui.radio(assemblies, value=assemblies[-1] if assemblies else None)
                .props("inline color=teal")
                .style("color: white;")
            )

        # Merge results
        with ui.row().classes("w-full mt-4 items-center").style("gap: 0;"):
            ui.element("div").style(
                "flex: 1; height: 1px; background-color: #cbd5e0;",
            )
            with ui.element("div").style(
                "background-color: white; border-radius: 8px; padding: 8px 16px; "
                "display: flex; align-items: center; gap: 8px;",
            ):
                ui.icon("swap_vert").style(
                    "color: #38ada9; font-size: 1.25rem;",
                )
                ui.label("Merge the results:").style(
                    "color: #333; font-size: 0.875rem; font-weight: 600;",
                )
                results_merge_radio = (
                    ui.radio(["union", "intersection"], value="union")
                    .props("inline color=teal dense")
                    .style("color: #333;")
                )
            ui.element("div").style(
                "flex: 1; height: 1px; background-color: #cbd5e0;",
            )

        # Boolean column filter
        ui.label("Filter by annotation:").classes("text-sm mb-2")
        bool_column_selector = (
            ui.select(
                options={
                    col: config.boolean_column_names[col]
                    for col in config.boolean_columns
                },
                multiple=True,
                label="Select annotations",
            )
            .props("outlined bg-color=white use-chips")
            .classes("w-full")
        )

        # The visitor's own lists, filled in once the browser has sent them.
        ui.label("Filter by my lists:").classes("text-sm mb-2 mt-2")
        my_lists_selector = (
            ui.select(
                options={},
                multiple=True,
                label="Select my lists",
            )
            .props("outlined bg-color=white use-chips")
            .classes("w-full")
        )

        def on_lists_change() -> None:
            labels: dict[str, str] = state.get("list_labels", {})
            my_lists_selector.set_options(
                dict(labels),
                value=[c for c in (my_lists_selector.value or []) if c in labels],
            )

        state.setdefault("on_lists_change", []).append(on_lists_change)

        # Merge annotations
        with ui.element("div").style(
            "border: 1px solid white; border-radius: 8px; padding: 8px 16px; "
            "display: flex; align-items: center; gap: 8px; margin-top: 8px;",
        ):
            ui.icon("merge_type").style("color: white; font-size: 1.25rem;")
            ui.label("Merge the lists:").classes("text-sm")
            annotations_merge_radio = (
                ui.radio(["union", "intersection"], value="union")
                .props("inline color=teal")
                .style("color: white;")
            )

        with ui.row().classes("gap-2 mt-4 justify-center w-full"):
            search_btn = ui.button(
                "Search",
                icon="search",
                on_click=perform_search,
            ).style("background-color: #38ada9;")

            create_search_trigger(f"c{search_btn.id}")

            ui.button("Clear", icon="clear", on_click=clear_search).props(
                "outline",
            )

    state["show_search_link"] = show_search_link

    # Left drawer toggle
    hidden_toggle_btn = ui.button("", on_click=drawer.toggle).style("display: none;")
    hidden_btn_id = f"c{hidden_toggle_btn.id}"

    ui.add_body_html(f"""
        <div id="search-panel-toggle" class="drawer-toggle-fixed" onclick="document.getElementById('{hidden_btn_id}').click();">
            <div class="toggle-text-container">
                <svg id="search-arrow-top" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="white" style="margin-bottom: 2px;">
                    <path d="M15.41 16.59L10.83 12l4.58-4.59L14 6l-6 6 6 6 1.41-1.41z"/>
                </svg>
                <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="white" style="margin-bottom: 4px;">
                    <path d="M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/>
                </svg>
                <span class="drawer-toggle-text">SEARCH</span>
                <svg id="search-arrow-bottom" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="white" style="margin-top: 2px;">
                    <path d="M15.41 16.59L10.83 12l4.58-4.59L14 6l-6 6 6 6 1.41-1.41z"/>
                </svg>
            </div>
        </div>
    """)

    # Store IDs for the drawer toggle JS (used by drawer_toggle.js)
    state["_left_toggle_id"] = "search-panel-toggle"
    state["_left_hidden_btn_id"] = hidden_btn_id

    return drawer
