"""The Compare view: gene-set comparison in the main area, beside the table.

It works on the tab's own state, so the current search results are a set
without saving them first:

* ``state["filtered_df"]`` -- the current results, offered while a search is
  active;
* ``state["base_df"]`` -- the database's Boolean gene lists (and, as
  ``mylist:`` columns, the visitor's lists);
* ``state["user_lists"]`` -- the visitor's lists themselves.

Everything numeric comes from :mod:`genetrek.services.compare`, every figure
from :mod:`genetrek.services.compare_figures` and every file from
:mod:`genetrek.services.compare_export`; this module only wires them to the
page. Like the right drawer's plots, nothing is computed while the view is
hidden: a change only marks it stale, and the work happens when it is shown.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl
from nicegui import ui

from genetrek.services import compare as cmp
from genetrek.services import compare_export as export
from genetrek.services import compare_figures as figures

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from genetrek.backgrounds import Background, Covariate
    from genetrek.config.loader import AppConfig
    from genetrek.ui.components.my_lists import MyLists

_TEAL = "#38ada9"
_NAVY = "#1a365d"
_RESULTS = "results"
_DB = "db:"
_LIST = "list:"
#: Genes listed by name in the region panel before "and N more".
_PREVIEW = 40
#: Stands in for an infinite odds ratio, which JSON cannot carry.
_INFINITE = 1e300

_FORMATS = {
    "SVG (vector)": ("svg", None),
    "PNG, 300 dpi": ("png", 300),
    "PNG, 600 dpi": ("png", 600),
}


class CompareView:
    """The Compare tab's content and behaviour."""

    def __init__(
        self,
        config: AppConfig,
        state: dict[str, Any],
        my_lists: MyLists,
        on_show_in_table: Callable[[frozenset[str], str], None],
    ) -> None:
        self.config = config
        self.state = state
        self.my_lists = my_lists
        self.on_show_in_table = on_show_in_table
        self.key = config.roles.primary_key
        self.symbol = config.roles.symbol

        self.selected: list[str] = []
        self.labels: dict[str, str] = {}
        self.background: Background = config.backgrounds[0]
        self.main_kind = "venn"
        self.heatmap_metric: figures.HeatmapMetric = "jaccard"
        #: The covariate comparisons are stratified by -- the first configured
        #: one -- and whether the heatmap shows the stratified result. The
        #: statistics table always shows both.
        self.covariate: Covariate | None = (
            config.covariates[0] if config.covariates else None
        )
        self.adjust = self.covariate is not None
        #: Keyed by background and the partial-coverage columns compared: the
        #: strata are computed within the analysed universe, which they shrink.
        self._strata: dict[tuple[str, tuple[str, ...]], dict[str, str]] = {}
        self.comparison: cmp.Comparison | None = None
        #: (members, exclusive) of the selected region, or None.
        self.region: tuple[tuple[int, ...], bool] | None = None
        self.visible = False
        self.stale = True
        self._database_sets: dict[str, frozenset[str]] = {}
        self._measured: dict[str, frozenset[str] | None] = {}
        self._universes: dict[str, frozenset[str]] = {}

        ui.add_body_html(
            "<script>\n"
            + (Path(__file__).parent.parent / "js" / "compare.js").read_text()
            + "\n</script>",
        )
        self._build_export_dialog()
        self.container = ui.column().classes("w-full gap-3")
        with self.container:
            self._build_controls()
            self._render_results()

        state.setdefault("on_filter_change", []).append(self._sources_changed)
        state.setdefault("on_lists_change", []).append(self._sources_changed)

    # -- sources ------------------------------------------------------------

    def _available(self) -> dict[str, str]:
        """Every set that can be compared now, id -> option text."""
        options: dict[str, str] = {}
        if self.key is None:
            return options
        results = self.state["filtered_df"]
        if 0 < results.height < self.state["base_df"].height:
            options[_RESULTS] = f"Current search results ({results.height:,} genes)"
        for column in self.config.boolean_columns:
            options[f"{_DB}{column}"] = (
                f"Database · {self.config.boolean_column_names.get(column, column)}"
            )
        for column, text in self.state.get("list_labels", {}).items():
            list_id = column.removeprefix("mylist:")
            options[f"{_LIST}{list_id}"] = f"My list · {text}"
        return options

    def _default_label(self, set_id: str) -> str:
        if set_id == _RESULTS:
            return "Search results"
        if set_id.startswith(_DB):
            column = set_id.removeprefix(_DB)
            return self.config.boolean_column_names.get(column, column)
        lst = self.state["user_lists"].get(set_id.removeprefix(_LIST))
        return lst.name if lst is not None else set_id

    def _genes(self, set_id: str) -> frozenset[str]:
        key = self.key
        if key is None:
            return frozenset()
        if set_id == _RESULTS:
            return cmp.keys_where(self.state["filtered_df"][key])
        if set_id.startswith(_DB):
            column = set_id.removeprefix(_DB)
            if column not in self._database_sets:
                frame = self.state["base_df"]
                self._database_sets[column] = cmp.keys_where(
                    frame.filter(pl.col(column))[key],
                )
            return self._database_sets[column]
        lst = self.state["user_lists"].get(set_id.removeprefix(_LIST))
        return lst.genes if lst is not None else frozenset()

    def _gene_set(self, set_id: str) -> cmp.GeneSet:
        label = self.labels.get(set_id) or self._default_label(set_id)
        if set_id == _RESULTS:
            return cmp.GeneSet(
                set_id, label, "Current search results", "results", self._genes(set_id)
            )
        if set_id.startswith(_DB):
            column = set_id.removeprefix(_DB)
            meta = self.config.fields.get(column)
            details = (
                {
                    "update": meta.update,
                    "doi": meta.doi,
                    "description": meta.description,
                    "universe": meta.universe,
                }
                if meta is not None
                else {}
            )
            if column not in self._measured and self.key is not None:
                self._measured[column] = cmp.measured_keys(
                    self.state["base_df"],
                    column,
                    self.key,
                )
            return cmp.GeneSet(
                set_id,
                label,
                self.config.boolean_column_names.get(column, column),
                "database",
                self._genes(set_id),
                column=column,
                details=details,
                measured=self._measured.get(column),
            )
        lst = self.state["user_lists"].get(set_id.removeprefix(_LIST))
        return cmp.GeneSet(
            set_id,
            label,
            lst.name if lst is not None else label,
            "my_list",
            self._genes(set_id),
            details={"created": lst.created} if lst is not None else {},
        )

    def _universe(self) -> frozenset[str]:
        name = self.background.name
        if name not in self._universes and self.key is not None:
            self._universes[name] = cmp.keys_where(
                self.config.df.filter(self.background.expr())[self.key],
            )
        return self._universes.get(name, frozenset())

    def _sources_changed(self) -> None:
        """A search or a list changed: re-offer, drop what vanished, recompute."""
        options = self._available()
        self.picker.set_options(options)
        kept = [s for s in self.selected if s in options]
        if kept != self.selected:
            self.selected = kept
            self.picker.set_value(kept)
        self.stale = True
        if self.visible:
            self.refresh()

    # -- controls -----------------------------------------------------------

    def _build_controls(self) -> None:
        with ui.card().classes("w-full"), ui.column().classes("w-full gap-2"):
            ui.label("Compare gene sets").classes("text-lg font-bold").style(
                f"color: {_NAVY};",
            )
            ui.label(
                "Pick 2 to 12 sets: the current search results, any gene list in "
                "the database, or your own lists.",
            ).classes("text-sm text-grey-8")
            with ui.row().classes("w-full items-start gap-4 no-wrap"):
                self.picker = (
                    ui.select(
                        self._available(),
                        multiple=True,
                        with_input=True,
                        label="Sets to compare",
                        on_change=lambda e: self._set_selection(e.value or []),
                    )
                    .props("outlined use-chips")
                    .classes("flex-grow")
                )
                self.background_select = (
                    ui.select(
                        [b.name for b in self.config.backgrounds],
                        value=self.background.name,
                        label="Background",
                        on_change=lambda e: self._set_background(e.value),
                    )
                    .props("outlined")
                    .style("min-width: 220px;")
                    .tooltip("The genes counted as the universe for every test")
                )
            if self.covariate is not None:
                ui.checkbox(
                    f"Colour the heatmap by the result adjusted for "
                    f"{self.covariate.name} (the table always shows both)",
                    value=self.adjust,
                    on_change=lambda e: self._set_adjust(value=bool(e.value)),
                ).props("dense").classes("text-sm").tooltip(
                    "Gene lists rich in constrained genes overlap partly for that "
                    "reason alone. The adjusted test compares them within strata of "
                    "the covariate (Cochran-Mantel-Haenszel).",
                )
            self.label_row = ui.row().classes("w-full items-center gap-2")

    def _render_label_inputs(self) -> None:
        self.label_row.clear()
        if not self.selected:
            return
        with self.label_row:
            ui.label("Labels in figures:").classes("text-sm text-grey-8")
            for set_id in self.selected:
                field = (
                    ui.input(
                        value=self.labels.get(set_id) or self._default_label(set_id)
                    )
                    .props("dense outlined maxlength=40")
                    .style("width: 200px;")
                )
                field.on(
                    "blur",
                    lambda _, f=field, i=set_id: self._set_label(i, f.value),
                )
                field.on(
                    "keydown.enter",
                    lambda _, f=field, i=set_id: self._set_label(i, f.value),
                )

    def _set_selection(self, values: Sequence[str]) -> None:
        values = list(values)
        if len(values) > cmp.MAX_SETS:
            ui.notify(f"At most {cmp.MAX_SETS} sets can be compared at once.")
            values = values[: cmp.MAX_SETS]
            self.picker.set_value(values)
            return
        self.selected = values
        self.region = None
        self._render_label_inputs()
        self.refresh()

    def _set_background(self, name: str) -> None:
        self.background = next(
            (b for b in self.config.backgrounds if b.name == name),
            self.config.backgrounds[0],
        )
        self.refresh()

    def _set_label(self, set_id: str, value: str) -> None:
        text = " ".join(str(value or "").split())[:40]
        if not text or self.labels.get(set_id) == text:
            return
        if text == self._default_label(set_id) and set_id not in self.labels:
            return
        self.labels[set_id] = text
        self.refresh()

    def open_with(self, set_ids: Sequence[str]) -> None:
        """Preselect sets, as the drawer's "Open in Compare" does."""
        available = self._available()
        wanted = [s for s in set_ids if s in available]
        self.picker.set_options(available)
        self.picker.set_value(wanted)
        self._set_selection(wanted)

    # -- computing and rendering --------------------------------------------

    def set_visible(self, *, visible: bool) -> None:
        self.visible = visible
        if visible and self.stale:
            self.refresh()

    def refresh(self) -> None:
        self.stale = True
        if not self.visible:
            return
        self._compute()
        self._render_results.refresh()
        self.stale = False

    def _strata_map(self, sets: list[cmp.GeneSet]) -> dict[str, str] | None:
        """Gene -> stratum of the covariate, within the analysed universe."""
        if self.covariate is None or self.key is None:
            return None
        partial = tuple(
            sorted(s.column for s in sets if s.measured is not None and s.column)
        )
        cache_key = (self.background.name, partial)
        if cache_key not in self._strata:
            analysed = cmp.effective_universe(sets, self._universe())
            self._strata[cache_key] = cmp.strata_for(
                self.config.df.filter(
                    pl.col(self.key).cast(pl.String).is_in(list(analysed)),
                ),
                self.key,
                self.covariate.column,
                self.covariate.bins,
            )
        return self._strata[cache_key]

    def _set_adjust(self, *, value: bool) -> None:
        self.adjust = value
        if self.comparison is not None:
            self._render_heatmap.refresh()

    def _compute(self) -> None:
        if len(self.selected) < cmp.MIN_SETS or self.key is None:
            self.comparison = None
            return
        sets = [self._gene_set(s) for s in self.selected]
        # Labels must be unique to tell the sets apart in a figure.
        unique = export.unique_headers([s.label for s in sets], [])
        sets = [replace(s, label=u) for s, u in zip(sets, unique, strict=True)]
        self.comparison = cmp.compare(
            sets,
            self._universe(),
            background=self.background.name,
            construction=cmp.construction_reasons(
                sets,
                self.config.fields,
                self.config.field_aliases,
            ),
            strata=self._strata_map(sets),
            covariate=self.covariate.name if self.covariate else "",
        )

    @ui.refreshable_method
    def _render_results(self) -> None:
        comparison = self.comparison
        if comparison is None:
            ui.label(
                "Choose at least two sets above to compare them.",
            ).classes("text-sm italic text-grey-7 p-4")
            return
        n = len(comparison.sets)

        notes = [
            f"{s.label}: {outside:,} of {before:,} genes are outside "
            f"“{comparison.background}” and were left out."
            for s, outside, before in zip(
                comparison.sets,
                comparison.outside_background,
                comparison.original_sizes,
                strict=True,
            )
            if outside
        ]
        if comparison.restricted_by:
            notes.append(
                f"Not every gene was measured by {', '.join(comparison.restricted_by)}: "
                f"the comparison uses the {comparison.universe_size:,} of the "
                f"{comparison.background_size:,} background genes that every set "
                f"measured, so a gene is never counted as absent from a set that "
                f"did not measure it.",
            )
        notes += [
            f"{s.label}: {unmeasured:,} of {before:,} genes were not measured by "
            f"another set and were left out."
            for s, unmeasured, before in zip(
                comparison.sets,
                comparison.not_measured,
                comparison.original_sizes,
                strict=True,
            )
            if unmeasured
        ]
        if comparison.coverage_warning:
            notes.append(f"Warning: {comparison.coverage_warning}")
        if any(not s.genes for s in comparison.sets):
            notes.append("A set with no genes in the background cannot be compared.")
        if notes:
            with (
                ui.column()
                .classes("w-full gap-0 p-2 rounded")
                .style(
                    "background-color: #fff8e1; border: 1px solid #f0c36d;",
                )
            ):
                for note in notes:
                    ui.label(note).classes("text-xs").style("color: #5f4b16;")

        if n > cmp.MAX_VENN_SETS:
            self.main_kind = "upset"
        elif n == cmp.MIN_SETS:
            self.main_kind = "venn"

        with ui.row().classes("w-full gap-3 items-stretch no-wrap"):
            with ui.card().classes("flex-grow").style("min-width: 0;"):
                self._render_main()
            with ui.card().style("width: 340px; flex: none;"):
                self._render_region()

        if n >= 3:  # noqa: PLR2004
            with ui.card().classes("w-full"):
                self._render_heatmap()

        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center justify-between"):
                within = comparison.background + (
                    " ∩ measured by every set" if comparison.restricted_by else ""
                )
                ui.label(
                    f"Pairwise statistics within {within} "
                    f"(N = {comparison.universe_size:,})",
                ).classes("font-bold")
                self._tables_menu()
            self._stats_table(comparison)
            ui.label(
                "OR: conditional maximum-likelihood odds ratio with its exact 95% "
                "CI, from the two-sided Fisher's exact test (as R's fisher.test); "
                "FDR: Benjamini-Hochberg across all pairs."
                + (
                    f" Adjusted: the same pair tested within strata of "
                    f"{comparison.covariate} (Cochran-Mantel-Haenszel; Mantel-"
                    f"Haenszel OR with Robins-Breslow-Greenland CI), its own FDR."
                    if comparison.covariate
                    else ""
                )
                + " Click a row to see the pair's shared genes.",
            ).classes("text-xs text-grey-7")

    # The two figure cards refresh on their own: re-rendering the whole results
    # area for a toggle rebuilt the page under the reader and jumped it back to
    # the top.

    @ui.refreshable_method
    def _render_main(self) -> None:
        comparison = self.comparison
        if comparison is None:
            return
        n = len(comparison.sets)
        with ui.row().classes("w-full items-center justify-between"):
            if 3 <= n <= cmp.MAX_VENN_SETS:  # noqa: PLR2004
                ui.toggle(
                    {"venn": "Venn", "upset": "UpSet"},
                    value=self.main_kind,
                    on_change=lambda e: self._set_main(e.value),
                ).props("dense no-caps color=grey-4 text-color=dark toggle-color=teal")
            else:
                ui.label("UpSet" if self.main_kind == "upset" else "Venn").classes(
                    "font-bold",
                )
            self._download_button(self.main_kind)
        figure = (
            figures.venn_figure(comparison)
            if self.main_kind == "venn"
            else figures.upset_figure(comparison)
        )
        plot = ui.plotly(_interactive(figure)).classes("w-full")
        plot.on("plotly_click", self._on_click)

    @ui.refreshable_method
    def _render_heatmap(self) -> None:
        comparison = self.comparison
        if comparison is None:
            return
        with ui.row().classes("w-full items-center justify-between"):
            ui.toggle(
                dict(figures.HEATMAP_METRICS),
                value=self.heatmap_metric,
                on_change=lambda e: self._set_metric(e.value),
            ).props("dense no-caps color=grey-4 text-color=dark toggle-color=teal")
            self._download_button("heatmap")
        # What the selected measure is and how to read it, right where it is
        # shown: the three answer different questions and are easy to misread.
        adjusted = self._heatmap_adjusted()
        ui.markdown(
            figures.HEATMAP_EXPLANATIONS[self.heatmap_metric]
            + (
                f" Shown **adjusted for {comparison.covariate}**: each pair tested "
                f"within its strata, so a shared bias towards constrained genes "
                f"does not count as overlap."
                if adjusted
                else ""
            )
            + (
                " A **\u2020** marks a pair that overlaps by construction."
                if any(p.construction for p in comparison.pairs)
                else ""
            ),
        ).classes("text-xs text-grey-8 w-full")
        heat = ui.plotly(
            _interactive(
                figures.heatmap_figure(
                    comparison,
                    self.heatmap_metric,
                    adjusted=adjusted,
                ),
            ),
        ).classes("w-full")
        heat.on("plotly_click", self._on_click)

    def _heatmap_adjusted(self) -> bool:
        """Whether the heatmap shows the stratified result: only the signed FDR
        has an adjusted form, and only when the comparison has a covariate."""
        return (
            self.adjust
            and self.heatmap_metric == "signed_neg_log10_fdr"
            and self.comparison is not None
            and bool(self.comparison.covariate)
        )

    def _set_main(self, kind: str) -> None:
        self.main_kind = kind
        self._render_main.refresh()

    def _set_metric(self, metric: str) -> None:
        self.heatmap_metric = metric  # type: ignore[assignment]
        self._render_heatmap.refresh()

    def _stats_table(self, comparison: cmp.Comparison) -> None:
        sets = comparison.sets

        def number(value: float, digits: int = 2) -> float | None:
            return None if math.isnan(value) else round(value, digits)

        rows = [
            {
                "id": f"{p.a},{p.b}",
                "a": sets[p.a].label,
                "b": sets[p.b].label,
                "n_a": p.n_a,
                "n_b": p.n_b,
                "overlap": p.overlap,
                "expected": number(p.expected, 1),
                "fold": number(p.fold),
                # JSON has no infinity; a zero cell's OR travels as a sentinel
                # the column's formatter prints as the symbol.
                "or": _INFINITE if math.isinf(p.odds_ratio) else number(p.odds_ratio),
                "ci": _interval(p.ci_lower, p.ci_upper),
                "p": p.p_value,
                "fdr": p.fdr,
                **(
                    {
                        "adj_or": _INFINITE
                        if math.isinf(p.adjusted.odds_ratio)
                        else number(p.adjusted.odds_ratio),
                        "adj_ci": _interval(p.adjusted.ci_lower, p.adjusted.ci_upper),
                        "adj_fdr": p.adjusted.fdr,
                    }
                    if p.adjusted is not None
                    else {}
                ),
                "jaccard": number(p.jaccard, 3),
                "ocoef": number(p.overlap_coefficient, 3),
                "note": p.construction,
            }
            for p in comparison.pairs
        ]
        sci = (
            "v => (v === null || v === undefined || Number.isNaN(v)) ? 'n/a' "
            ": v === 0 ? '< 1e-300' "
            ": (v < 0.001 ? v.toExponential(2) : v.toFixed(3))"
        )
        odds = f"v => v === null ? 'n/a' : v >= {_INFINITE} ? '\\u221e' : v"
        adjusted_columns: list[dict[str, Any]] = (
            [
                {
                    "name": "adj_or",
                    "label": f"OR adj. ({comparison.covariate})",
                    "field": "adj_or",
                    "sortable": True,
                    ":format": odds,
                },
                {"name": "adj_ci", "label": "Adj. 95% CI", "field": "adj_ci"},
                {
                    "name": "adj_fdr",
                    "label": "Adj. FDR",
                    "field": "adj_fdr",
                    "sortable": True,
                    ":format": sci,
                },
            ]
            if comparison.covariate
            else []
        )
        columns: list[dict[str, Any]] = [
            {"name": "a", "label": "Set A", "field": "a", "align": "left"},
            {"name": "b", "label": "Set B", "field": "b", "align": "left"},
            {"name": "n_a", "label": "|A|", "field": "n_a", "sortable": True},
            {"name": "n_b", "label": "|B|", "field": "n_b", "sortable": True},
            {
                "name": "overlap",
                "label": "Overlap",
                "field": "overlap",
                "sortable": True,
            },
            {
                "name": "expected",
                "label": "Expected",
                "field": "expected",
                "sortable": True,
            },
            {"name": "fold", "label": "Fold", "field": "fold", "sortable": True},
            {
                "name": "or",
                "label": "OR",
                "field": "or",
                "sortable": True,
                ":format": odds,
            },
            {"name": "ci", "label": "95% CI", "field": "ci"},
            {"name": "p", "label": "p", "field": "p", "sortable": True, ":format": sci},
            {
                "name": "fdr",
                "label": "FDR",
                "field": "fdr",
                "sortable": True,
                ":format": sci,
            },
            *adjusted_columns,
            {
                "name": "jaccard",
                "label": "Jaccard",
                "field": "jaccard",
                "sortable": True,
            },
            {
                "name": "ocoef",
                "label": "Overlap coef.",
                "field": "ocoef",
                "sortable": True,
            },
            {"name": "note", "label": "Note", "field": "note", "align": "left"},
        ]
        table = (
            ui.table(columns=columns, rows=rows, row_key="id", pagination=0)
            .props("dense flat wrap-cells")
            .classes("w-full")
        )
        table.on(
            "rowClick",
            lambda e: self._select_pair(str(e.args[1].get("id", ""))),
        )

    # -- regions --------------------------------------------------------------

    def _on_click(self, e: Any) -> None:  # noqa: ANN401
        points = (e.args or {}).get("points") or []
        if not points:
            return
        data = points[0].get("customdata")
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, str) or not data:
            return
        if data.startswith("pair:"):
            self._select_pair(data.removeprefix("pair:"))
            return
        try:
            members = tuple(int(i) for i in data.split(","))
        except ValueError:
            return
        self.region = (members, True)
        self._render_region.refresh()

    def _select_pair(self, text: str) -> None:
        try:
            members = tuple(int(i) for i in text.split(","))
        except ValueError:
            return
        self.region = (members, False)
        self._render_region.refresh()

    def _region_genes(self) -> tuple[frozenset[str], str]:
        comparison = self.comparison
        if comparison is None or self.region is None:
            return frozenset(), ""
        members, exclusive = self.region
        if any(i >= len(comparison.sets) for i in members):
            return frozenset(), ""
        if exclusive:
            region = comparison.region(members)
            genes = region.genes if region is not None else frozenset()
            return genes, comparison.region_label(members)
        genes = frozenset.intersection(*(comparison.sets[i].genes for i in members))
        names = " & ".join(comparison.sets[i].label for i in members)
        return genes, f"{names} (shared)"

    @ui.refreshable_method
    def _render_region(self) -> None:
        genes, label = self._region_genes()
        if self.region is None or not label:
            ui.label("Region").classes("font-bold")
            ui.label(
                "Click a region of the diagram, an UpSet bar, a heatmap cell or a "
                "statistics row to list its genes.",
            ).classes("text-sm text-grey-7")
            return
        ui.label(label).classes("font-bold").style(f"color: {_NAVY};")
        ui.label(f"{len(genes):,} gene(s)").classes("text-sm")
        symbols = self._symbols(genes)
        if symbols:
            shown = ", ".join(symbols[:_PREVIEW])
            more = (
                f" and {len(symbols) - _PREVIEW:,} more"
                if len(symbols) > _PREVIEW
                else ""
            )
            ui.label(shown + more).classes("text-xs text-grey-8").style(
                "max-height: 220px; overflow-y: auto;",
            )
        with ui.column().classes("w-full gap-1 mt-2"):
            ui.button(
                "Show in table",
                icon="table_rows",
                on_click=lambda: self.on_show_in_table(genes, label),
            ).props("dense no-caps").style(f"background-color: {_TEAL}; color: white;")
            if self.my_lists.enabled:
                ui.button(
                    "Save as list",
                    icon="bookmark_add",
                    on_click=lambda: self._save_region(genes, label),
                ).props("dense no-caps outline").style(f"color: {_TEAL};")
            ui.button(
                "Download genes (TSV)",
                icon="download",
                on_click=lambda: self._download_region(genes, label),
            ).props("dense no-caps outline").style(f"color: {_TEAL};")
        if genes:
            return
        ui.label("This region is empty.").classes("text-xs italic text-grey-7")

    def _symbols(self, genes: frozenset[str]) -> list[str]:
        if not genes or self.key is None:
            return []
        frame = export.region_table(genes, self.config.df, self.key, self.symbol)
        column = self.symbol or export.SYMBOL_FALLBACK
        return [s for s in frame[column].to_list() if s]

    def _save_region(self, genes: frozenset[str], label: str) -> None:
        if not genes:
            ui.notify("This region has no genes to save.")
            return
        self.my_lists.save_genes(label, genes, source="compare")

    def _download_region(self, genes: frozenset[str], label: str) -> None:
        if self.key is None:
            return
        frame = export.region_table(genes, self.config.df, self.key, self.symbol)
        ui.download.content(
            export.tsv_bytes(frame),
            f"genetrek-region-{_slug(label)}.tsv",
            "text/tab-separated-values",
        )

    # -- downloads ------------------------------------------------------------

    def _tables(self) -> tuple[dict[str, pl.DataFrame], str] | None:
        comparison = self.comparison
        if comparison is None or self.key is None:
            return None
        tables = {
            "genesets": export.genesets_table(
                comparison, self.config.df, self.key, self.symbol
            ),
            "sets": export.sets_table(comparison, self.config.release),
            "statistics": export.statistics_table(comparison),
        }
        methods = export.methods_text(
            comparison,
            release=self.config.release,
            background_definition=self.background.describe(),
            today=date.today(),  # noqa: DTZ011
            covariate_definition=self.covariate.describe() if self.covariate else "",
        )
        return tables, methods

    def _alt_texts(self) -> dict[str, str]:
        """Alt text for every figure this comparison draws, for the bundle."""
        comparison = self.comparison
        if comparison is None:
            return {}
        n = len(comparison.sets)
        texts: dict[str, str] = {}
        if n <= cmp.MAX_VENN_SETS:
            texts["Venn diagram"] = figures.alt_text(comparison, "venn")
        if n >= 3:  # noqa: PLR2004
            texts["UpSet plot"] = figures.alt_text(comparison, "upset")
            texts["Pairwise heatmap"] = figures.alt_text(
                comparison,
                "heatmap",
                self.heatmap_metric,
                adjusted=self._heatmap_adjusted(),
            )
        return texts

    def _tables_menu(self) -> None:
        with (
            ui.button("Download tables", icon="table_view")
            .props(
                "dense no-caps outline",
            )
            .style(f"color: {_TEAL};"),
            ui.menu(),
        ):
            for name, text in (
                ("genesets", "Gene sets — gene by set membership (TSV)"),
                ("sets", "Sets — what each set is (TSV)"),
                ("statistics", "Statistics — pairwise tests (TSV)"),
            ):
                ui.menu_item(text, on_click=lambda _, n=name: self._download_table(n))
            ui.separator()
            ui.menu_item(
                "All tables + methods + alt text + API request (XLSX)",
                on_click=lambda: self._download_bundle("xlsx"),
            )
            ui.menu_item(
                "All tables + README + alt text + request.json (ZIP)",
                on_click=lambda: self._download_bundle("zip"),
            )

    def _download_table(self, name: str) -> None:
        built = self._tables()
        if built is None:
            return
        ui.download.content(
            export.tsv_bytes(built[0][name]),
            f"genetrek-compare-{name}-{date.today():%Y%m%d}.tsv",  # noqa: DTZ011
            "text/tab-separated-values",
        )

    def _download_bundle(self, kind: str) -> None:
        built = self._tables()
        if built is None:
            return
        tables, methods = built
        assert self.comparison is not None  # noqa: S101 -- _tables checked it
        alt_texts = self._alt_texts()
        # The exact POST /api/compare body that regenerates these tables.
        request = export.request_body(self.comparison)
        stem = f"genetrek-compare-{date.today():%Y%m%d}"  # noqa: DTZ011
        if kind == "xlsx":
            ui.download.content(
                export.xlsx_bytes(
                    tables, methods, alt_texts=alt_texts, request=request
                ),
                f"{stem}.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            ui.download.content(
                export.zip_bytes(tables, methods, alt_texts=alt_texts, request=request),
                f"{stem}.zip",
                "application/zip",
            )

    def _download_button(self, kind: str) -> None:
        ui.button(
            "Download figure",
            icon="image",
            on_click=lambda: self._open_export(kind),
        ).props("dense no-caps outline").style(f"color: {_TEAL};")

    def _build_export_dialog(self) -> None:
        self._export_kind = "venn"
        self.export_dialog = ui.dialog()
        with self.export_dialog, ui.card().style("width: 560px; max-width: 94vw;"):
            self._export_title = (
                ui.label()
                .classes("text-lg font-bold")
                .style(
                    f"color: {_NAVY};",
                )
            )
            self._export_width = (
                ui.select(
                    list(figures.WIDTH_PRESETS_MM),
                    value=next(iter(figures.WIDTH_PRESETS_MM)),
                    label="Width",
                )
                .props("outlined dense")
                .classes("w-full")
            )
            self._export_format = (
                ui.select(
                    list(_FORMATS),
                    value=next(iter(_FORMATS)),
                    label="Format",
                )
                .props("outlined dense")
                .classes("w-full")
            )
            ui.label(
                "Arial 7 pt labels, 0.5 pt lines, colour-blind-safe palette, RGB. "
                "SVG keeps text editable; convert it to PDF or CMYK if a journal "
                "asks.",
            ).classes("text-xs text-grey-7")
            # Journals ask for alt text with every figure; it is generated from
            # the numbers drawn, so it is factual and stays true.
            self._export_alt = (
                ui.textarea("Alt text for this figure")
                .props("outlined dense readonly autogrow")
                .classes("w-full text-xs")
            )
            with ui.row().classes("w-full justify-end"):
                ui.button(
                    "Copy alt text",
                    icon="content_copy",
                    on_click=self._copy_alt,
                ).props("flat dense no-caps").style(f"color: {_TEAL};")
                ui.button("Cancel", on_click=self.export_dialog.close).props("flat")
                ui.button("Download", icon="download", on_click=self._export).style(
                    f"background-color: {_TEAL}; color: white;",
                )

    def _copy_alt(self) -> None:
        ui.clipboard.write(self._export_alt.value or "")
        ui.notify("Alt text copied.")

    def _open_export(self, kind: str) -> None:
        self._export_kind = kind
        self._export_title.set_text(
            {
                "venn": "Venn diagram",
                "upset": "UpSet plot",
                "heatmap": "Pairwise heatmap",
            }[kind],
        )
        if self.comparison is not None:
            self._export_alt.set_value(
                figures.alt_text(
                    self.comparison,
                    kind,
                    self.heatmap_metric,
                    adjusted=self._heatmap_adjusted(),
                ),
            )
        self.export_dialog.open()

    def _export(self) -> None:
        comparison = self.comparison
        if comparison is None:
            return
        width_mm = figures.WIDTH_PRESETS_MM[self._export_width.value]
        fmt, dpi = _FORMATS[self._export_format.value]
        style = figures.export_style(width_mm)
        kind = self._export_kind
        if kind == "venn":
            figure = figures.venn_figure(comparison, style)
        elif kind == "upset":
            figure = figures.upset_figure(comparison, style)
        else:
            figure = figures.heatmap_figure(
                comparison,
                self.heatmap_metric,
                style,
                adjusted=self._heatmap_adjusted(),
            )
        options = {
            "format": fmt,
            "scale": figures.png_scale(dpi) if dpi else 1,
            "filename": f"genetrek-{kind}-{int(width_mm)}mm"
            + (f"-{dpi}dpi" if dpi else ""),
        }
        self.export_dialog.close()
        ui.run_javascript(
            f"window.gtCompare && window.gtCompare.download({figure.to_json()}, "
            f"{json.dumps(options)});",
        )


def _interval(lower: float, upper: float) -> str:
    """``1.23 to 4.56``; an end at infinity shown as the symbol."""
    if math.isnan(lower) or math.isnan(upper):
        return "n/a"

    def end(value: float) -> str:
        return "\u221e" if math.isinf(value) else f"{value:.2f}"

    return f"{end(lower)} to {end(upper)}"


def _interactive(figure: Any) -> dict:  # noqa: ANN401
    """The figure as NiceGUI's plotly element takes it, without Plotly's toolbar.

    The toolbar's own PNG button would export the screen style at screen size;
    "Download figure" is the publication export, so it is the only one offered.
    """
    return {
        **figure.to_plotly_json(),
        "config": {"displayModeBar": False, "responsive": True},
    }


def _slug(text: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "-" for c in text)
    return "-".join(part for part in out.split("-") if part)[:60] or "region"
