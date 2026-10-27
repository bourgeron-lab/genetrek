"""Plotly figures for a gene-set comparison: Venn, UpSet, pairwise heatmap.

One function per figure, each taking a :class:`~genetrek.services.compare.Comparison`
and a :class:`FigureStyle`. The screen and the publication export are the same
function with a different style, so what is downloaded is what was looked at --
only the typography, size and interactivity change.

Publication sizing works in millimetres. Plotly lays out in CSS pixels, 96 to
the inch, so an SVG of ``mm_to_px(89)`` pixels opens at exactly 89 mm wide, and
a PNG rendered at ``scale = dpi / 96`` has the requested resolution at that
width. Font sizes are given in points and converted the same way.

Clickable regions carry their member indices in ``customdata`` as a string
like ``"0,2"`` (exclusive region) or ``"pair:0,2"`` (a pair's whole
intersection), which the Compare view reads back from ``plotly_click``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

import plotly.graph_objects as go

from genetrek.services.compare_layout import venn_layout

if TYPE_CHECKING:
    from collections.abc import Mapping

    from genetrek.services.compare import Comparison, PairStat
    from genetrek.services.compare_layout import Layout

#: Okabe & Ito's colour-blind-safe qualitative palette, black last.
OKABE_ITO = (
    "#E69F00",
    "#56B4E9",
    "#009E73",
    "#D55E00",
    "#0072B2",
    "#CC79A7",
    "#F0E442",
    "#000000",
)

#: Journal column widths, in millimetres (Nature, Cell and most others agree).
WIDTH_PRESETS_MM: dict[str, float] = {
    "Single column (89 mm)": 89.0,
    "1.5 columns (120 mm)": 120.0,
    "Double column (183 mm)": 183.0,
}
DPI_CHOICES = (300, 600)

_CSS_DPI = 96.0
_PT = _CSS_DPI / 72.0  # CSS px per typographic point

#: Most intersections an UpSet plot shows, largest first. The rest still exist
#: in the tables; a bar chart of hundreds of one-gene bars says nothing.
MAX_UPSET_BARS = 40

#: A region whose label point is closer than this to its edge, as a fraction
#: of the drawing's span, is labelled from outside.
_MIN_LABEL_ROOM = 0.035

HeatmapMetric = Literal["jaccard", "overlap_coefficient", "signed_neg_log10_fdr"]
HEATMAP_METRICS: dict[str, str] = {
    "jaccard": "Jaccard index",
    "overlap_coefficient": "Overlap coefficient",
    # A real minus sign, for print.
    "signed_neg_log10_fdr": "Signed \u2212log10 FDR",
}

#: What each heatmap metric measures and how to read it, shown under the
#: heatmap. ``resources/content/documentation.md`` says the same at length --
#: keep the two in step.
HEATMAP_EXPLANATIONS: dict[str, str] = {
    "jaccard": (
        "**Jaccard index** = shared genes \u00f7 genes in either set "
        "(|A \u2229 B| / |A \u222a B|). 0 means no gene in common, 1 means identical "
        "sets. It measures how *similar* two sets are, so it stays low when a "
        "small list sits entirely inside a large one."
    ),
    "overlap_coefficient": (
        "**Overlap coefficient** = shared genes \u00f7 size of the smaller set "
        "(|A \u2229 B| / min(|A|, |B|)). 1 means the smaller set lies entirely "
        "inside the larger, whatever their sizes. It measures *nesting*, and "
        "says nothing about how different the two sizes are."
    ),
    "signed_neg_log10_fdr": (
        "**Signed \u2212log10 FDR**: how strongly the overlap departs from chance "
        "within the analysed universe -- the background, less any gene a set "
        "did not measure (Fisher's exact test, Benjamini\u2013Hochberg-"
        "adjusted across all pairs), and in which direction: **positive (blue) "
        "when the sets share more genes than expected, negative (orange) when "
        "fewer**. \u00b11.3 is FDR 0.05, \u00b12 is 0.01, \u00b13 is 0.001; "
        "\u00b1>300 is beyond what can be computed. It measures *evidence*, not "
        "size: a modest overlap between two large lists can score very high."
    ),
}

#: Diverging scale for the signed metric: Okabe-Ito vermillion for depletion,
#: white at zero, Okabe-Ito blue for enrichment. Readable in colour-blind
#: vision and still ordered when printed in greyscale.
_DIVERGING = [[0.0, "#D55E00"], [0.5, "#FFFFFF"], [1.0, "#0072B2"]]
#: Smallest half-range of the signed scale, so a comparison where nothing is
#: significant is drawn pale rather than stretched to full colour.
_SIGNED_FLOOR = 2.0


def mm_to_px(mm: float) -> float:
    """CSS pixels for a physical width."""
    return mm / 25.4 * _CSS_DPI


def png_scale(dpi: int) -> float:
    """Plotly's ``scale`` for a PNG at ``dpi`` when laid out in CSS pixels."""
    return dpi / _CSS_DPI


@dataclass(frozen=True)
class FigureStyle:
    font_family: str
    font_size: float  # px
    title_size: float  # px
    line_width: float  # px
    fill_opacity: float
    export: bool
    width: float | None = None  # px; None lets the page size it
    palette: tuple[str, ...] = OKABE_ITO


SCREEN = FigureStyle(
    font_family="Roboto, Helvetica, Arial, sans-serif",
    font_size=13.0,
    title_size=15.0,
    line_width=2.0,
    fill_opacity=0.22,
    export=False,
)


def export_style(width_mm: float) -> FigureStyle:
    """Publication style: Arial 7 pt labels, 8 pt titles, 0.5 pt lines."""
    return FigureStyle(
        font_family="Arial, Helvetica, sans-serif",
        font_size=7 * _PT,
        title_size=8 * _PT,
        line_width=0.5 * _PT,
        fill_opacity=0.28,
        export=True,
        width=mm_to_px(width_mm),
    )


def _layout(style: FigureStyle, *, aspect: float, margin: Mapping[str, float]) -> dict:
    layout: dict = {
        "font": {"family": style.font_family, "size": style.font_size, "color": "#000"},
        "paper_bgcolor": "#ffffff",
        "plot_bgcolor": "#ffffff",
        "margin": dict(margin),
        "showlegend": False,
        "hoverlabel": {"font": {"family": style.font_family}},
    }
    if style.width is not None:
        layout["width"] = style.width
        layout["height"] = style.width * aspect
    else:
        layout["autosize"] = True
        layout["height"] = 460
    return layout


def _hidden_axis() -> dict:
    return {"visible": False, "showgrid": False, "zeroline": False}


def _fmt(n: int) -> str:
    return f"{n:,}"


# -- Venn -----------------------------------------------------------------------


def _venn(comparison: Comparison) -> Layout:
    """The Venn layout, given every region's count so none is left without area."""
    return venn_layout(
        [len(s.genes) for s in comparison.sets],
        {(p.a, p.b): p.overlap for p in comparison.pairs},
        {r.members: r.size for r in comparison.regions},
    )


def venn_figure(comparison: Comparison, style: FigureStyle = SCREEN) -> go.Figure:
    """Euler (2-3 sets) or classic Venn (4-5 sets) diagram with region counts."""
    sets = comparison.sets
    sizes = [len(s.genes) for s in sets]
    layout = _venn(comparison)
    counts = {r.members: r.size for r in comparison.regions}

    xmin, xmax, ymin, ymax = layout.bounds
    # Room for the set names, which sit outside the shapes.
    # Set names go in a legend below, not beside the shapes: a long list name
    # beside a circle runs off the figure at any width a journal allows.
    padx, pady = 0.04 * (xmax - xmin), 0.04 * (ymax - ymin)
    xr = (xmin - padx, xmax + padx)
    yr = (ymin - pady, ymax + pady)

    shapes = []
    for i, shape in enumerate(layout.shapes):
        colour = style.palette[i % len(style.palette)]
        shapes.append(
            {
                "type": "path",
                "path": shape.svg_path(),
                "fillcolor": colour,
                "opacity": style.fill_opacity,
                "line": {"width": 0},
                "layer": "below",
            },
        )
        # A separate, opaque outline: drawn with the fill it would be faded too.
        shapes.append(
            {
                "type": "path",
                "path": shape.svg_path(),
                "line": {"color": colour, "width": style.line_width},
                "fillcolor": "rgba(0,0,0,0)",
            },
        )

    annotations = []
    cx = (xmin + xmax) / 2
    cy = (ymin + ymax) / 2
    pulled = 0
    for members, (x, y) in layout.labels.items():
        label = {
            "x": x,
            "y": y,
            "text": _fmt(counts.get(members, 0)),
            "showarrow": False,
            "font": {"size": style.font_size},
        }
        if layout.room.get(members, 1.0) < _MIN_LABEL_ROOM:
            # Too shallow to hold its count -- a 14-gene set beside 2,000, say.
            # Label it from outside, pointing in, staggering neighbours.
            # Fanned out across the leaders' direction (0, +1, -1, +2, ...), so
            # neighbouring small regions -- which point the same way -- do not
            # stack their counts on top of each other.
            dx, dy = x - cx, y - cy
            norm = math.hypot(dx, dy) or 1.0
            ux, uy = dx / norm, -dy / norm  # screen y points down
            step = (pulled + 1) // 2 * (1 if pulled % 2 else -1)
            pulled += 1
            reach = style.font_size * 3.4
            spread = style.font_size * 1.5 * step
            label.update(
                {
                    "showarrow": True,
                    "arrowhead": 0,
                    "arrowwidth": max(style.line_width * 0.6, 0.6),
                    "arrowcolor": "#444",
                    "ax": ux * reach - uy * spread,
                    "ay": uy * reach + ux * spread,
                },
            )
        annotations.append(label)
    note = (
        "Areas are not proportional to set sizes."
        if not layout.proportional
        else (
            "Circle areas are proportional to set sizes; overlap areas are approximate."
            if len(sets) == 3  # noqa: PLR2004
            else "Areas are proportional to set sizes."
        )
    )
    annotations.append(
        {
            "x": 0,
            "y": 1,
            "xref": "paper",
            "yref": "paper",
            "xanchor": "left",
            "yanchor": "bottom",
            "text": note,
            "showarrow": False,
            "font": {"size": style.font_size * 0.85, "color": "#555"},
        },
    )

    fig = go.Figure()
    for i, s in enumerate(sets):
        colour = style.palette[i % len(style.palette)]
        fig.add_trace(
            go.Scatter(
                x=[None],
                y=[None],
                mode="markers",
                # Keyword form on purpose: the marker-shape key written as a quoted
                # dict key trips the role guard in tests/test_roles.py, which takes
                # it for the gene-symbol column named by literal.
                marker=go.scatter.Marker(
                    symbol="square",
                    size=style.font_size * 1.1,
                    color=colour,
                    line={"color": colour, "width": style.line_width},
                ),
                name=f"{s.label} ({_fmt(sizes[i])})",
                showlegend=True,
                hoverinfo="skip",
            ),
        )
    if not style.export:
        # Shapes are not clickable in Plotly, so each region gets an invisible
        # marker at its label point that is.
        regions = list(layout.labels.items())
        fig.add_trace(
            go.Scatter(
                x=[p[0] for _, p in regions],
                y=[p[1] for _, p in regions],
                mode="markers",
                marker={"size": 26, "opacity": 0},
                showlegend=False,
                customdata=[",".join(map(str, m)) for m, _ in regions],
                text=[
                    f"{comparison.region_label(m)}: {_fmt(counts.get(m, 0))} genes"
                    for m, _ in regions
                ],
                hovertemplate="%{text}<extra></extra>",
            ),
        )

    aspect = (yr[1] - yr[0]) / (xr[1] - xr[0])
    # One legend line per set, under the diagram; the note sits above it.
    legend_px = len(sets) * style.font_size * 1.6 + style.font_size
    top_px = style.font_size * 2.0
    fig_layout = _layout(
        style,
        aspect=aspect,
        margin={"l": 4, "r": 4, "t": top_px, "b": legend_px},
    )
    if style.width is not None:
        fig_layout["height"] += legend_px + top_px
    fig_layout.update(
        {
            "xaxis": {**_hidden_axis(), "range": list(xr)},
            "yaxis": {**_hidden_axis(), "range": list(yr), "scaleanchor": "x"},
            "shapes": shapes,
            "annotations": annotations,
            "showlegend": True,
            "legend": {
                "orientation": "v",
                "x": 0,
                "xanchor": "left",
                "y": 0,
                "yanchor": "top",
                "font": {"size": style.font_size},
                "itemclick": False,
                "itemdoubleclick": False,
                "tracegroupgap": 0,
            },
        },
    )
    fig.update_layout(fig_layout)
    return fig


# -- UpSet ----------------------------------------------------------------------


def upset_figure(comparison: Comparison, style: FigureStyle = SCREEN) -> go.Figure:
    """Intersection sizes, the membership matrix beneath, set sizes to the left.

    Laid out on explicit axis domains rather than ``make_subplots``: the matrix
    and the set-size bars share rows, the intersection bars and the matrix share
    columns, and nothing else is shared -- an empty corner subplot tied into a
    shared axis wrecked its autorange. The set names sit in their own gutter
    between the set-size bars and the matrix, sized from the longest name.
    """
    sets = comparison.sets
    n = len(sets)
    regions = [r for r in comparison.regions if r.size][:MAX_UPSET_BARS]
    k = max(len(regions), 1)

    # On screen the plot's width is the browser's to decide, so the name gutter
    # is sized for a narrow one: too wide wastes a little space, too narrow
    # cuts the names off.
    width_px = style.width or 440.0
    longest = max(len(s.label) for s in sets)
    names_frac = min(0.42, (longest * style.font_size * 0.58 + 12) / width_px)
    bars_frac = 0.16
    matrix_left = bars_frac + names_frac
    matrix_frac = min(0.55, 0.12 + 0.07 * n)
    gap = 0.03

    ink = "#222" if style.export else "#2c5282"
    xs = list(range(len(regions)))
    custom = [",".join(map(str, r.members)) for r in regions]
    hover = [f"{comparison.region_label(r.members)}: {_fmt(r.size)}" for r in regions]
    small = style.font_size * 0.85

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=xs,
            y=[r.size for r in regions],
            marker={"color": ink},
            text=[_fmt(r.size) for r in regions],
            textposition="outside",
            cliponaxis=False,
            textfont={"size": small},
            customdata=custom,
            hovertext=hover,
            hovertemplate="%{hovertext}<extra></extra>",
            xaxis="x",
            yaxis="y",
        ),
    )
    dot = max(style.font_size * 0.9, 5)
    fig.add_trace(
        go.Scatter(
            x=[x for x in xs for _ in range(n)],
            y=[y for _ in xs for y in range(n)],
            mode="markers",
            marker={"size": dot, "color": "#d9d9d9"},
            hoverinfo="skip",
            xaxis="x",
            yaxis="y2",
        ),
    )
    for x, region in zip(xs, regions, strict=True):
        fig.add_trace(
            go.Scatter(
                x=[x] * len(region.members),
                y=list(region.members),
                mode="lines+markers",
                line={"color": ink, "width": style.line_width * 1.5},
                marker={"size": dot, "color": ink},
                customdata=[custom[x]] * len(region.members),
                hovertext=[hover[x]] * len(region.members),
                hovertemplate="%{hovertext}<extra></extra>",
                xaxis="x",
                yaxis="y2",
            ),
        )
    fig.add_trace(
        go.Bar(
            y=list(range(n)),
            x=[len(s.genes) for s in sets],
            orientation="h",
            marker={"color": "#888"},
            text=[_fmt(len(s.genes)) for s in sets],
            textposition="outside",
            cliponaxis=False,
            textfont={"size": small},
            customdata=[s.label for s in sets],
            hovertemplate="%{customdata}: %{x:,} genes<extra></extra>",
            xaxis="x2",
            yaxis="y3",
        ),
    )

    tick_font = {"size": style.font_size}
    rows = {"range": [n - 0.5, -0.5], "showgrid": False, "zeroline": False}
    hidden = len(comparison.regions) - len(regions)
    note_px = small * 2.2 if hidden else 0
    layout = _layout(
        style,
        aspect=0.62,
        # Left room for the largest set's size, printed past its bar's end.
        margin={"l": 6 + small * 3.4, "r": 6, "t": small * 1.6, "b": 6 + note_px},
    )
    layout.update(
        {
            "bargap": 0.3,
            "xaxis": {
                "domain": [matrix_left, 1.0],
                "range": [-0.6, k - 0.4],
                "visible": False,
                "anchor": "y2",
            },
            "yaxis": {
                "domain": [matrix_frac + gap, 1.0],
                "anchor": "x",
                "rangemode": "tozero",
                "title": {"text": "Intersection size", "font": tick_font},
                "tickfont": tick_font,
                "showgrid": not style.export,
                "gridcolor": "#eee",
                "zeroline": False,
                "showline": style.export,
                "linewidth": style.line_width,
                "ticks": "outside" if style.export else "",
            },
            "yaxis2": {
                **rows,
                "domain": [0.0, matrix_frac],
                "anchor": "x",
                "tickvals": list(range(n)),
                "ticktext": [s.label for s in sets],
                "tickfont": tick_font,
                "ticks": "",
            },
            "xaxis2": {
                "domain": [0.0, bars_frac],
                "anchor": "y3",
                "autorange": "reversed",
                "showgrid": False,
                "zeroline": False,
                "tickfont": {"size": small},
                "nticks": 3,
                "title": {"text": "Set size", "font": {"size": small}},
            },
            "yaxis3": {
                **rows,
                "domain": [0.0, matrix_frac],
                "anchor": "x2",
                "visible": False,
            },
        },
    )
    if hidden:
        layout["annotations"] = [
            {
                "x": 0,
                "y": 0,
                "xref": "paper",
                "yref": "paper",
                "yanchor": "top",
                "xanchor": "left",
                "yshift": -note_px * 0.4,
                "showarrow": False,
                "text": f"The {len(regions)} largest of {len(comparison.regions)} "
                "intersections are shown.",
                "font": {"size": small, "color": "#555"},
            },
        ]
    fig.update_layout(layout)
    return fig


# -- pairwise heatmap -------------------------------------------------------------


def _metric(pair: PairStat, metric: str, *, adjusted: bool) -> float:
    if metric == "signed_neg_log10_fdr":
        use = pair.adjusted if adjusted and pair.adjusted is not None else None
        fdr = use.fdr if use is not None else pair.fdr
        if math.isnan(fdr):
            return float("nan")
        enriched = use.odds_ratio > 1 if use is not None else pair.enriched
        magnitude = -math.log10(max(fdr, 1e-300))
        return magnitude if enriched else -magnitude
    return float(getattr(pair, metric))


def heatmap_figure(
    comparison: Comparison,
    metric: HeatmapMetric = "jaccard",
    style: FigureStyle = SCREEN,
    *,
    adjusted: bool = False,
) -> go.Figure:
    """Every pair, one cell each, coloured and labelled by ``metric``.

    The matrix is symmetric and its diagonal blank: a set compared with
    itself says nothing. A pair overlapping by construction carries a dagger.
    With ``adjusted`` the signed FDR is the covariate-stratified one; Jaccard
    and the overlap coefficient describe the sets themselves and do not change.
    """
    signed = metric == "signed_neg_log10_fdr"
    sets = comparison.sets
    n = len(sets)
    z: list[list[float | None]] = [[None] * n for _ in range(n)]
    text: list[list[str]] = [[""] * n for _ in range(n)]
    custom: list[list[str]] = [[""] * n for _ in range(n)]
    flagged = False
    for pair in comparison.pairs:
        value = _metric(pair, metric, adjusted=adjusted)
        label = "" if math.isnan(value) else f"{value:.2f}"
        if signed and abs(value) >= 300:  # noqa: PLR2004
            # The test underflows below ~1e-300: 300 is a floor, not a value.
            label = ">300" if value > 0 else "<\u2212300"
        if pair.construction:
            label += "†"
            flagged = True
        for i, j in ((pair.a, pair.b), (pair.b, pair.a)):
            z[i][j] = None if math.isnan(value) else value
            text[i][j] = label
            custom[i][j] = f"pair:{pair.a},{pair.b}"

    labels = [s.label for s in sets]
    values = [abs(v) for row in z for v in row if v is not None]
    half = max([_SIGNED_FLOOR, *values])
    title = HEATMAP_METRICS[metric]
    if signed and adjusted and comparison.covariate:
        title += f"<br>(by {comparison.covariate})"
    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=labels,
            y=labels,
            text=text,
            texttemplate="%{text}",
            textfont={"size": style.font_size * (0.9 if n <= 6 else 0.75)},  # noqa: PLR2004
            customdata=custom,
            colorscale=_DIVERGING if signed else "Blues",
            zmin=-half if signed else 0,
            zmid=0 if signed else None,
            zmax=half if signed else 1,
            colorbar={
                "title": {"text": title, "side": "right"},
                "thickness": max(style.font_size, 8),
                "tickfont": {"size": style.font_size * 0.85},
            },
            hovertemplate="%{y} \u00d7 %{x}: %{text}<extra></extra>",
            xgap=1,
            ygap=1,
        ),
    )
    # Right margin for the colour bar's title, which is written vertically and
    # runs to two lines when it names the covariate.
    right = style.font_size * (4.2 if "<br>" in title else 2.6)
    layout = _layout(style, aspect=0.82, margin={"l": 8, "r": right, "t": 8, "b": 8})
    layout.update(
        {
            "xaxis": {"side": "bottom", "tickangle": -40, "showgrid": False},
            "yaxis": {"autorange": "reversed", "showgrid": False, "scaleanchor": "x"},
        },
    )
    if flagged:
        layout["annotations"] = [
            {
                "x": 0,
                "y": 1,
                "xref": "paper",
                "yref": "paper",
                "yanchor": "bottom",
                "xanchor": "left",
                "showarrow": False,
                "text": "† overlap expected by construction",
                "font": {"size": style.font_size * 0.85, "color": "#555"},
            },
        ]
        layout["margin"] = {**layout["margin"], "t": style.font_size * 2.2}
    fig.update_layout(layout)
    return fig


def with_width(style: FigureStyle, width_mm: float) -> FigureStyle:
    """``style`` at a publication width."""
    return replace(style, width=mm_to_px(width_mm))


# -- alt text ---------------------------------------------------------------------


def alt_text(
    comparison: Comparison,
    kind: str,
    metric: HeatmapMetric = "jaccard",
    *,
    adjusted: bool = False,
) -> str:
    """A factual description of a figure, for a journal's alt-text field.

    It states what is drawn and the numbers in it -- no interpretation -- so it
    stays true whatever the reader concludes.
    """
    sets = comparison.sets
    within = (
        f"within the background {comparison.background!r} "
        + (
            "restricted to the genes every set measured "
            if comparison.restricted_by
            else ""
        )
        + f"({comparison.universe_size:,} genes)"
    )
    names = "; ".join(f"{s.label} ({len(s.genes):,} genes)" for s in sets)
    if kind == "venn":
        layout = (
            "areas proportional to set sizes"
            if _venn(comparison).proportional
            else "fixed shapes, areas not proportional"
        )
        regions = "; ".join(
            f"{comparison.region_label(r.members)}: {r.size:,}"
            for r in comparison.regions
        )
        return (
            f"Venn diagram ({layout}) of {len(sets)} gene sets {within}: {names}. "
            f"Genes in each region: {regions}."
        )
    if kind == "upset":
        drawn = [r for r in comparison.regions if r.size][:MAX_UPSET_BARS]
        bars = "; ".join(
            f"{comparison.region_label(r.members)}: {r.size:,}" for r in drawn
        )
        more = (
            f" ({len(drawn)} of {len(comparison.regions)} intersections shown)"
            if len(drawn) < len(comparison.regions)
            else ""
        )
        return (
            f"UpSet plot of {len(sets)} gene sets {within}: {names}. "
            f"Intersection sizes, largest first{more}: {bars}."
        )
    # Mid-sentence, so lower case -- except Jaccard, a name.
    label = {
        "jaccard": "Jaccard index",
        "overlap_coefficient": "overlap coefficient",
        "signed_neg_log10_fdr": "signed \u2212log10 FDR",
    }[metric]
    if metric == "signed_neg_log10_fdr" and adjusted and comparison.covariate:
        label += f", stratified by {comparison.covariate}"
    cells = []
    for pair in comparison.pairs:
        value = _metric(pair, metric, adjusted=adjusted)
        shown = "not defined" if math.isnan(value) else f"{value:.2f}"
        note = " (overlap by construction)" if pair.construction else ""
        cells.append(f"{sets[pair.a].label} and {sets[pair.b].label}: {shown}{note}")
    reading = (
        " Positive values mark more shared genes than expected, negative fewer."
        if metric == "signed_neg_log10_fdr"
        else ""
    )
    return (
        f"Heatmap of the {label} for every pair of {len(sets)} gene sets {within}: "
        f"{'; '.join(cells)}.{reading}"
    )
