"""Right drawer: statistics, enrichment analysis, and Plotly charts."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from nicegui import ui

from genetrek.services.enrichment import compute_enrichment

if TYPE_CHECKING:
    from collections.abc import Callable

    from nicegui.events import ValueChangeEventArguments

    from genetrek.config.loader import AppConfig


#: Quasar's drawer slide, in seconds, plus a frame. A figure that blocks the
#: main thread must not start building until the slide has finished: the toggle
#: button tracks the drawer for a fixed wall-clock window (ui/js/drawer_toggle.js),
#: and a build that overruns it used to leave the button stranded at the
#: viewport edge until the drawer was closed and reopened.
_DRAW_DELAY = 0.4


def _plot_placeholder() -> None:
    """Stand in for a figure that has not been built yet.

    The figures are no longer built at page construction, so a first open shows
    empty space under each heading for as long as the build takes -- about a
    second on the full table. Saying so is better than looking broken. Both
    renderers clear their container first, so this is replaced by the figure.
    """
    with ui.row().classes("items-center gap-2 py-8"):
        ui.spinner(size="sm")
        ui.label("Building plot...").classes("text-sm italic")


def _after_the_slide(delay: float, callback: Callable[[], None]) -> None:
    """Run ``callback`` once, ``delay`` seconds from now, on this client."""
    ui.timer(delay, callback, once=True)


class _DeferredPlot:
    """A figure drawn when the drawer first shows it, not at page construction.

    Both figures carry one point per gene, so building them eagerly put ~2.5 MB
    of coordinates and hover data into every page load -- for a drawer that
    starts closed and that most visitors never open. Deferring costs two flags:
    whether the drawer is currently showing the plot, and whether the search
    results have moved since it was last drawn.

    A filter change while the drawer is closed therefore does no work at all;
    it only notes that the figure is stale, and the redraw happens on the next
    open. That is the same reason the eager draw was worth removing: a search
    used to rebuild a figure nobody was looking at.
    """

    def __init__(
        self,
        draw: Callable[[], None],
        schedule: Callable[[float, Callable[[], None]], None] = _after_the_slide,
    ) -> None:
        self._draw = draw
        self._schedule = schedule
        self._showing = False
        self._stale = True

    def invalidate(self) -> None:
        """Note that the results changed; redraw only if the plot is visible."""
        self._stale = True
        if self._showing:
            self._redraw()

    def set_showing(self, *, showing: bool) -> None:
        """Follow the drawer, drawing on first sight and after any change.

        The draw is scheduled rather than immediate, so the drawer's slide
        finishes before the figure starts building. See :data:`_DRAW_DELAY`.
        """
        self._showing = showing
        if showing and self._stale:
            self._schedule(_DRAW_DELAY, self._redraw_if_still_showing)

    def _redraw_if_still_showing(self) -> None:
        """Drop the scheduled draw if the drawer was closed again meanwhile."""
        if self._showing and self._stale:
            self._redraw()

    def _redraw(self) -> None:
        self._draw()
        self._stale = False


def create_stats_drawer(
    config: AppConfig,
    state: dict[str, Any],
) -> ui.right_drawer:
    """Create the statistics/plots right drawer. Returns the drawer element."""
    #: Figures that wait for the drawer to open. Populated below.
    deferred: list[_DeferredPlot] = []

    with (  # noqa: SIM117
        ui.right_drawer(value=False)
        .classes("text-white p-4 relative")
        .props("width=500")
        .style("background-color: #2c5282;") as stats_drawer
    ):
        with ui.column().classes("w-full"):
            with ui.row().classes("items-center mb-4"):
                ui.icon("analytics").classes("text-2xl")
                ui.label("Statistics & Plots").classes("text-xl font-bold ml-2")

            # The universe is chosen, and named, rather than implied: this label
            # used to say "protein-coding" while the test counted every gene.
            backgrounds = {b.name: b for b in config.backgrounds}
            with ui.row().classes("w-full items-center gap-2 no-wrap mb-1"):
                ui.label("Enrichment within").classes("text-sm")
                background_selector = (
                    ui.select(
                        list(backgrounds),
                        value=config.backgrounds[0].name,
                    )
                    .props("dense outlined bg-color=white")
                    .classes("flex-grow")
                )
            ui.label("of the current results in:").classes("text-sm mb-1")

            enrichment_selector = (
                ui.select(
                    options={
                        col: config.boolean_column_names[col]
                        for col in config.boolean_columns
                    },
                    label="Select annotation",
                )
                .props("outlined bg-color=white")
                .classes("w-full")
            )

            enrichment_results = ui.column().classes("w-full mt-4")

            def on_enrichment_change() -> None:
                selected_annotation = enrichment_selector.value
                if not selected_annotation:
                    return

                filtered_df = state["filtered_df"]
                total_genes = len(config.df)
                search_genes = len(filtered_df)

                if search_genes in (0, total_genes):
                    enrichment_results.clear()
                    with enrichment_results:
                        ui.label(
                            "Please perform a search first to compute enrichment.",
                        ).classes("text-sm italic")
                    return

                result = compute_enrichment(
                    state["base_df"],
                    filtered_df,
                    selected_annotation,
                    config.roles,
                    backgrounds.get(background_selector.value),
                )
                _render_enrichment_results(enrichment_results, result)
                open_compare = state.get("open_compare")
                if open_compare is not None:
                    set_id = (
                        f"list:{selected_annotation.removeprefix('mylist:')}"
                        if selected_annotation.startswith("mylist:")
                        else f"db:{selected_annotation}"
                    )
                    with enrichment_results:
                        ui.button(
                            "Open in Compare",
                            icon="join_inner",
                            on_click=lambda: open_compare(set_id),
                        ).props("dense no-caps outline color=white").classes("mt-2")

            enrichment_selector.on(
                "update:model-value",
                lambda: on_enrichment_change(),
            )
            background_selector.on_value_change(lambda _: on_enrichment_change())

            database_options = {
                col: config.boolean_column_names[col] for col in config.boolean_columns
            }

            def on_lists_change() -> None:
                """Offer the visitor's lists after the database's own."""
                options = {**database_options, **state.get("list_labels", {})}
                value = enrichment_selector.value
                enrichment_selector.set_options(
                    options,
                    value=value if value in options else None,
                )

            state.setdefault("on_lists_change", []).append(on_lists_change)

            ui.separator().classes("my-4").style("background-color: #cbd5e0;")

            # pLI vs LOEUF scatter plot
            ui.label("Loss-of-function deleteriousness scores").classes(
                "text-lg font-bold mb-1",
            )
            scatter_plot_container = ui.column().classes("w-full")
            with scatter_plot_container:
                _plot_placeholder()

            def update_scatter_plot() -> None:
                _render_scatter_plot(
                    scatter_plot_container,
                    state["filtered_df"],
                    config.roles.symbol,
                )

            scatter_plot = _DeferredPlot(update_scatter_plot)
            state["on_filter_change"].append(scatter_plot.invalidate)
            deferred.append(scatter_plot)

            ui.separator().classes("my-4").style("background-color: #cbd5e0;")

            # Autism odds ratio scatter plot
            ui.label("Autism odds ratio associated to LoF variants").classes(
                "text-lg font-bold mb-1",
            )
            autism_or_plot_container = ui.column().classes("w-full")
            with autism_or_plot_container:
                _plot_placeholder()

            def update_autism_or_plot() -> None:
                _render_autism_or_plot(
                    autism_or_plot_container,
                    state["filtered_df"],
                    config.roles.symbol,
                )

            autism_or_plot = _DeferredPlot(update_autism_or_plot)
            state["on_filter_change"].append(autism_or_plot.invalidate)
            deferred.append(autism_or_plot)

    # Right drawer toggle
    hidden_stats_toggle_btn = ui.button("", on_click=stats_drawer.toggle).style(
        "display: none;",
    )
    hidden_stats_btn_id = f"c{hidden_stats_toggle_btn.id}"

    ui.add_body_html(f"""
        <div id="stats-panel-toggle" class="drawer-toggle-fixed-right" onclick="document.getElementById('{hidden_stats_btn_id}').click();">
            <div class="toggle-text-container">
                <svg id="stats-arrow-top" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="white" style="margin-bottom: 2px;">
                    <path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6-1.41-1.41z"/>
                </svg>
                <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="white" style="margin-bottom: 4px; transform: rotate(-90deg);">
                    <path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zM9 17H7v-7h2v7zm4 0h-2V7h2v10zm4 0h-2v-4h2v4z"/>
                </svg>
                <span class="drawer-toggle-text">STATISTICS</span>
                <svg id="stats-arrow-bottom" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="white" style="margin-top: 2px;">
                    <path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6-1.41-1.41z"/>
                </svg>
            </div>
        </div>
    """)

    def on_drawer_toggle(event: ValueChangeEventArguments) -> None:
        for plot in deferred:
            plot.set_showing(showing=bool(event.value))

    # Bound to the drawer's own value rather than to the toggle button, so a
    # close by any other route is noticed too.
    stats_drawer.on_value_change(on_drawer_toggle)

    state["_right_toggle_id"] = "stats-panel-toggle"
    state["_right_hidden_btn_id"] = hidden_stats_btn_id

    return stats_drawer


def _render_enrichment_results(
    container: ui.column,
    result: Any,  # noqa: ANN401
) -> None:
    """Render enrichment analysis results into the container."""
    container.clear()
    with container, ui.row().classes("w-full gap-4 items-start"):
        # Contingency table
        with ui.column().classes("flex-1"):
            ui.label("Contingency Table:").classes("text-sm font-bold mb-1")
            with ui.element("div").style(
                "display: grid; grid-template-columns: auto auto auto; gap: 1px; "
                "background-color: white; border-radius: 8px; overflow: hidden; font-size: 0.875rem;",
            ):
                _header_cell("")
                _header_cell("In set")
                _header_cell("Not in set")
                _row_header("In search")
                _data_cell(str(result.a))
                _data_cell(str(result.b))
                _row_header("Not in search")
                _data_cell(str(result.c))
                _data_cell(str(result.d))

        # Fisher results
        with ui.column().classes("flex-1"):
            ui.label("Fisher Exact Test:").classes("text-sm font-bold mb-1")
            with ui.element("div").style(
                "display: grid; grid-template-columns: auto 1fr; gap: 1px; "
                "background-color: white; border-radius: 8px; overflow: hidden; font-size: 0.875rem;",
            ):
                _row_header("OR:")
                _data_cell(f"{result.odds_ratio:.3f}")
                _row_header("95% CI:")
                if not math.isnan(result.ci_lower):
                    _data_cell(
                        f"[{result.ci_lower:.3f}, {result.ci_upper:.3f}]",
                    )
                else:
                    with ui.element("div").style(
                        "background-color: #f7fafc; padding: 6px; color: #333;",
                    ):
                        ui.label("N/A (zero cell)").classes("italic")
                _row_header("P-value:")
                _data_cell(f"{result.p_value:.2e}")
    if result.n_not_measured:
        with container:
            ui.label(
                f"{result.n_not_measured:,} genes this annotation did not measure "
                f"are left out of the universe (N = "
                f"{result.a + result.b + result.c + result.d:,}).",
            ).classes("text-xs italic")


def _header_cell(text: str) -> None:
    with ui.element("div").style(
        "background-color: #e2e8f0; padding: 6px; font-weight: 600; color: #333; text-align: center;",
    ):
        ui.label(text)


def _row_header(text: str) -> None:
    with ui.element("div").style(
        "background-color: #e2e8f0; padding: 6px; font-weight: 600; color: #333;",
    ):
        ui.label(text)


def _data_cell(text: str) -> None:
    with ui.element("div").style(
        "background-color: #f7fafc; padding: 6px; text-align: center; color: #333;",
    ):
        ui.label(text)


#: Candidate x-axis columns for the constraint plot, best first.
#:
#: s-het is the current measure. ``pli`` is the fallback so a database built
#: before s-het arrived still plots -- removable once nothing carries it. Both
#: are probabilities/coefficients on 0..1 where higher means more constrained,
#: so the axis range and the reading of the plot are the same either way.
_CONSTRAINT_X = (("shet_mean", "s-het (posterior mean)"), ("pli", "pLI"))

#: Optional column the constraint plot colours its points by.
_CONSTRAINT_COLOUR = "is_shet_constrained"

#: Neutral marker colour, used when there is no constrained/not call to show.
_NEUTRAL = "#2c5282"

#: Marker colour for a value the axis cannot hold, matching the constrained red.
_UNBOUNDED = "#c53030"

#: Where an unbounded value sits, as a multiple of the largest measured one,
#: and how much room the axis leaves above it so the marker is not clipped.
_UNBOUNDED_POSITION = 1.08
_UNBOUNDED_HEADROOM = 1.12

#: Decimals kept in plotted coordinates. See :func:`_rounded`.
_COORDINATE_DIGITS = 4


def _rounded(values: list[Any], digits: int = _COORDINATE_DIGITS) -> list[Any]:
    """Round plotted coordinates, which arrive carrying spurious precision.

    The measurement columns are ``Float32``; serialising them to JSON widens
    them to float64 and prints the conversion noise -- ``1.229200005531311``
    for a value that is really 1.2292. On a 350px-tall plot whose hover shows
    three decimals, four is already finer than anything visible, and it takes
    roughly 63% off the coordinate arrays.
    """
    return [None if v is None else round(v, digits) for v in values]


def _render_scatter_plot(
    container: ui.column,
    filtered_df: Any,  # noqa: ANN401
    symbol_col: str | None,
) -> None:
    """Render the constraint scatter plot: selection coefficient against LOEUF.

    The measurement columns are named in ``schema.CODE_DEPENDENT_COLUMNS`` and so
    are asked for by literal name; the label column is not, coming from
    ``roles.symbol`` so a database calling it ``hgnc_symbol`` still plots.

    There is deliberately no vertical guide line. pLI had one at 0.9, but the
    published s-het constrained call is not a threshold on s-het at all --
    constrained genes span 0.073-0.948 and unconstrained 0.000016-0.160, and they
    overlap -- so the points are coloured by that published call instead of being
    divided by a cutoff the source does not use.
    """
    import plotly.graph_objects as go  # noqa: PLC0415

    container.clear()

    if symbol_col is None:
        with container:
            ui.label(
                "This database has no gene symbol column to label points with.",
            ).classes("text-sm italic")
        return

    x_col, x_label = next(
        ((col, label) for col, label in _CONSTRAINT_X if col in filtered_df.columns),
        (None, ""),
    )
    if x_col is None:
        with container:
            ui.label(
                f"Missing columns: one of {' or '.join(c for c, _ in _CONSTRAINT_X)}",
            ).classes("text-sm italic")
        return

    required = [x_col, "loeuf", "moeuf", symbol_col]
    if missing := [col for col in required if col not in filtered_df.columns]:
        with container:
            ui.label(f"Missing columns: {', '.join(missing)}").classes(
                "text-sm italic",
            )
        return

    # The colour column is optional and its nulls are kept: on a database where
    # the x axis is pLI, a gene may have gnomAD constraint but no s-het estimate,
    # and dropping those would quietly shrink the plot.
    colour_col = (
        _CONSTRAINT_COLOUR if _CONSTRAINT_COLOUR in filtered_df.columns else None
    )
    plot_df = filtered_df.select(
        [*required, *([colour_col] if colour_col else [])],
    ).drop_nulls(required)

    if len(plot_df) == 0:
        with container:
            ui.label(
                "No data available for plotting (all values are null)",
            ).classes("text-sm italic")
        return

    fig = go.Figure()
    for name, colour, group in _constraint_groups(plot_df, colour_col):
        if len(group) == 0:
            continue
        # The hover label is a template rather than a rendered string per
        # point: x and y are already in the coordinate arrays, so a per-point
        # label repeated the axis name, the field names and both values for
        # every gene -- 1.8 MB of it on this trace pair. Only the symbol and
        # MOEUF are not otherwise present, so only those travel per point.
        fig.add_trace(
            go.Scatter(
                x=_rounded(group[x_col].to_list()),
                y=_rounded(group["loeuf"].to_list()),
                mode="markers",
                marker={"color": colour, "size": 8, "opacity": 0.7},
                customdata=[
                    [sym, moeuf]
                    for sym, moeuf in zip(
                        group[symbol_col].to_list(),
                        _rounded(group["moeuf"].to_list()),
                        strict=True,
                    )
                ],
                hovertemplate=(
                    f"<b>%{{customdata[0]}}</b><br>{x_label}: %{{x:.3f}}"
                    "<br>LOEUF: %{y:.3f}<br>MOEUF: %{customdata[1]:.3f}"
                    # Suppresses the trace-name box, which `hoverinfo="text"`
                    # did not show.
                    "<extra></extra>"
                ),
                name=name,
            ),
        )

    fig.add_hline(
        y=0.35,
        line_dash="dash",
        line_color="black",
        line_width=1,
        annotation_text="LOEUF = 0.35",
        annotation_position="right",
    )
    loeuf_vals = plot_df["loeuf"].to_list()
    fig.update_layout(
        xaxis_title=x_label,
        yaxis_title="LOEUF",
        xaxis={"range": [-0.05, 1.05], "gridcolor": "#e2e8f0"},
        yaxis={
            "range": [0, max(2, max(loeuf_vals) * 1.1) if loeuf_vals else 2],
            "gridcolor": "#e2e8f0",
        },
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin={"l": 50, "r": 20, "t": 30, "b": 50},
        height=350,
        showlegend=colour_col is not None,
        legend={"orientation": "h", "y": 1.12, "x": 0},
        hovermode="closest",
    )

    with container:
        ui.plotly(fig).classes("w-full")


def _constraint_groups(
    plot_df: Any,  # noqa: ANN401
    colour_col: str | None,
) -> list[tuple[str, str, Any]]:
    """Split the points into (legend name, colour, rows) triples.

    Three groups when a published constrained call is available; one neutral
    group when it is not, so a database without it looks exactly as the plot did
    before the colouring was added.
    """
    import polars as pl  # noqa: PLC0415

    if colour_col is None:
        return [("Genes", _NEUTRAL, plot_df)]
    return [
        ("s-het constrained", "#c53030", plot_df.filter(pl.col(colour_col))),
        ("not constrained", _NEUTRAL, plot_df.filter(pl.col(colour_col).not_())),
        (
            "no s-het estimate",
            "#a0aec0",
            plot_df.filter(pl.col(colour_col).is_null()),
        ),
    ]


def _render_autism_or_plot(
    container: ui.column,
    filtered_df: Any,  # noqa: ANN401
    symbol_col: str | None,
) -> None:
    """Render the autism odds ratio scatter plot.

    As with the constraint plot, the three measurement columns are name-bound but
    the label column comes from ``roles.symbol``.
    """
    import plotly.graph_objects as go  # noqa: PLC0415

    carriers_col = "37365347_number_of_carriers_among_autistic_individuals"
    or_col = "37365347_autism_or"
    pval_col = "37365347_p_value"

    required_cols = [
        carriers_col,
        or_col,
        pval_col,
        *([symbol_col] if symbol_col else []),
    ]
    missing_cols = [col for col in required_cols if col not in filtered_df.columns]

    container.clear()

    if symbol_col is None:
        with container:
            ui.label(
                "This database has no gene symbol column to label points with."
            ).classes(
                "text-sm italic",
            )
        return

    if missing_cols:
        with container:
            ui.label(f"Missing columns: {', '.join(missing_cols)}").classes(
                "text-sm italic",
            )
        return

    plot_df = filtered_df.select(
        [symbol_col, carriers_col, or_col, pval_col],
    ).drop_nulls()

    if len(plot_df) == 0:
        with container:
            ui.label(
                "No data available for plotting (all values are null)",
            ).classes("text-sm italic")
        return

    rows = list(
        zip(
            plot_df[symbol_col].to_list(),
            plot_df[carriers_col].to_list(),
            plot_df[or_col].to_list(),
            plot_df[pval_col].to_list(),
            strict=True,
        ),
    )
    # An odds ratio of infinity comes from a zero cell in the 2x2, not from a
    # missing measurement: these genes have carriers and a real p-value, and
    # some of them are strong results. `drop_nulls` above keeps them, and they
    # used to serialise to JSON null and silently vanish from the plot. They
    # are drawn at the top of the axis instead, with their own marker so the
    # position is not read as a measurement of that size.
    #
    # A NaN odds ratio is neither finite nor infinite and falls out here, which
    # is right -- that is not a measurement at all.
    measured = [row for row in rows if math.isfinite(row[2])]
    unbounded = [row for row in rows if math.isinf(row[2]) and row[2] > 0]

    measured_or = [m[2] for m in measured]
    y_max = max(measured_or) if measured_or else 1.0
    clamp_at = round(y_max * _UNBOUNDED_POSITION, _COORDINATE_DIGITS)

    fig = go.Figure()
    if measured:
        fig.add_trace(
            go.Scatter(
                x=[m[1] for m in measured],
                y=_rounded([m[2] for m in measured]),
                mode="markers",
                marker={"color": _NEUTRAL, "size": 8, "opacity": 0.7},
                customdata=[[m[0], m[3]] for m in measured],
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>Carriers: %{x}"
                    "<br>Odds Ratio: %{y:.3f}<br>P-value: %{customdata[1]:.2e}"
                    "<extra></extra>"
                ),
                name="Genes",
            ),
        )
    if unbounded:
        fig.add_trace(
            go.Scatter(
                x=[u[1] for u in unbounded],
                y=[clamp_at] * len(unbounded),
                mode="markers",
                marker={"color": _UNBOUNDED, "size": 10, "opacity": 0.85},
                # Plotly's magic-underscore form rather than a marker-shape
                # key in the dict above: tests/test_roles.py scans this package
                # for identifier column names as string literals, and that
                # guard is worth more intact than this line is worth either way.
                marker_symbol="triangle-up",
                customdata=[[u[0], u[3]] for u in unbounded],
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>Carriers: %{x}"
                    "<br>Odds Ratio: \u221e<br>P-value: %{customdata[1]:.2e}"
                    "<extra></extra>"
                ),
                name="odds ratio unbounded",
            ),
        )
    fig.update_layout(
        xaxis_title="Number of carriers among autistic individuals",
        yaxis_title="Autism Odds Ratio",
        xaxis={"gridcolor": "#e2e8f0"},
        yaxis={
            "gridcolor": "#e2e8f0",
            "range": [0, y_max * _UNBOUNDED_HEADROOM],
        },
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin={"l": 50, "r": 20, "t": 30, "b": 50},
        height=350,
        # Only worth a legend when there is a second marker shape to explain.
        showlegend=bool(unbounded),
        legend={"orientation": "h", "y": 1.12, "x": 0},
        hovermode="closest",
    )

    with container:
        ui.plotly(fig).classes("w-full")
