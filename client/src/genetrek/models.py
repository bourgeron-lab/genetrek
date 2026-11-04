"""Results that carry more than a table: comparisons, and where they came from."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

_RATIOS = {"Inf": math.inf, "-Inf": -math.inf, "NaN": math.nan}


def ratio(value: object) -> float | None:
    """A ratio as the server sends it: a number, ``"Inf"``/``"-Inf"``, or null."""
    if value is None:
        return None
    if isinstance(value, str):
        return _RATIOS.get(value, math.nan)
    return float(value)  # type: ignore[arg-type]


def frame(records: list[dict[str, Any]]) -> pl.DataFrame:
    """A DataFrame from JSON records, typed from every row, nulls kept."""
    if not records:
        return pl.DataFrame()
    return pl.DataFrame(records, infer_schema_length=None)


def _pairs(pairs: list[dict[str, Any]]) -> pl.DataFrame:
    rows = []
    for pair in pairs:
        low, high = (pair.get("ci95") or [None, None])[:2]
        adjusted = pair.get("adjusted") or {}
        adj_low, adj_high = (adjusted.get("ci95") or [None, None])[:2]
        rows.append(
            {
                "set_a": pair["set_a"],
                "set_b": pair["set_b"],
                "n_a": pair["n_a"],
                "n_b": pair["n_b"],
                "overlap": pair["overlap"],
                "expected_overlap": pair.get("expected_overlap"),
                "fold_enrichment": pair.get("fold_enrichment"),
                "odds_ratio": ratio(pair.get("odds_ratio")),
                "ci95_low": ratio(low),
                "ci95_high": ratio(high),
                "p_value": pair.get("p_value"),
                "fdr": pair.get("fdr"),
                "jaccard": pair.get("jaccard"),
                "overlap_coefficient": pair.get("overlap_coefficient"),
                "overlap_by_construction": pair.get("overlap_by_construction", ""),
                "adjusted_for": adjusted.get("adjusted_for"),
                # Why there is no adjusted result (a set defined from the
                # covariate), or a caveat on the one given (API 1.2).
                "adjusted_unavailable": pair.get("adjusted_unavailable", ""),
                "adjusted_caveat": adjusted.get("caveat", ""),
                "adjusted_odds_ratio": ratio(adjusted.get("odds_ratio")),
                "adjusted_ci95_low": ratio(adj_low),
                "adjusted_ci95_high": ratio(adj_high),
                "adjusted_p_value": adjusted.get("p_value"),
            },
        )
    return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()


@dataclass(frozen=True)
class Comparison:
    """A ``POST /api/compare`` result.

    ``pairs`` has one row per pair of sets: overlap, expected overlap, fold
    enrichment, the conditional odds ratio with its exact 95% interval,
    Fisher's p-value, the BH FDR, and the same adjusted for the covariate
    (``adjusted_*``) -- crude and adjusted side by side, as the site shows them.
    An infinite odds ratio is ``inf``; a p-value of 0 means below ~1e-300.

    Attributes:
        request: The body that was sent -- :meth:`to_request_json` writes it,
            and :meth:`GeneTrek.replay` sends it again.
        response: The server's whole answer, as JSON.
        server: The URL that answered.
        client_version: This package's version.
    """

    request: dict[str, Any]
    response: dict[str, Any]
    server: str
    client_version: str
    pairs: pl.DataFrame = field(init=False)
    sets: pl.DataFrame = field(init=False)
    regions: pl.DataFrame = field(init=False)

    def __post_init__(self) -> None:
        response = self.response
        object.__setattr__(self, "pairs", _pairs(response.get("pairs", [])))
        object.__setattr__(self, "sets", frame(response.get("sets", [])))
        regions = [
            {**region, "sets": " & ".join(region.get("sets", []))}
            for region in response.get("regions", [])
        ]
        object.__setattr__(self, "regions", frame(regions))

    @property
    def release(self) -> str:
        """The database release the numbers were computed on."""
        return str(self.response.get("release", ""))

    @property
    def key(self) -> str:
        """The column gene keys are values of, e.g. ``hgnc_id``."""
        return str(self.response.get("key", ""))

    @property
    def background(self) -> dict[str, Any]:
        """The background, with its size before and after any restriction."""
        return dict(self.response.get("background", {}))

    @property
    def adjusted_for(self) -> list[str]:
        """The covariate the adjusted columns are stratified on."""
        return list(self.response.get("adjusted_for", []))

    @property
    def methods(self) -> str:
        """The methods paragraph, as the site's downloads carry it."""
        return str(self.response.get("methods", ""))

    @property
    def warnings(self) -> list[str]:
        """What to know before quoting the numbers."""
        return list(self.response.get("warnings", []))

    def to_request_json(self, path: str | Path | None = None) -> str:
        """The request body as JSON; written to ``path`` when given."""
        text = json.dumps(self.request, indent=1, ensure_ascii=False)
        if path is not None:
            Path(path).write_text(text + "\n", encoding="utf-8")
        return text

    def __repr__(self) -> str:
        labels = ", ".join(self.sets["label"].to_list()) if self.sets.height else ""
        return (
            f"<Comparison of {self.sets.height} sets ({labels}) against "
            f"{self.background.get('name', '?')}, release {self.release}>"
        )
