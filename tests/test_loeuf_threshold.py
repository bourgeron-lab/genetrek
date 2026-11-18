"""The constraint plot's LOEUF line comes from the table's colour scale.

It used to be a literal 0.35 (gnomAD v2's cut-off) while the table, the
``is_loeuf_gnomad_v4_1_1`` flag and the badge all said 0.45 (gnomAD v4's).
"""

from __future__ import annotations

from genetrek_server.ui.components.stats_drawer import loeuf_threshold

_SCALE = {"scale": [{"below": 0.45, "color": "#c53030"}, {"color": "#2b6cb0"}]}


def test_threshold_is_the_scales_below_bound() -> None:
    assert loeuf_threshold({"loeuf": _SCALE}) == 0.45


def test_first_below_bound_wins() -> None:
    scale = {
        "scale": [
            {"above": 2, "color": "#000"},
            {"below": 0.3, "color": "#111"},
            {"below": 0.6, "color": "#222"},
        ]
    }
    assert loeuf_threshold({"loeuf": scale}) == 0.3


def test_scale_keyed_by_an_alias() -> None:
    assert loeuf_threshold({"constraint": _SCALE}, ["constraint"]) == 0.45


def test_no_scale_draws_no_line() -> None:
    assert loeuf_threshold({}) is None
    assert loeuf_threshold({"loeuf": {"x": {"label": "X"}}}) is None
    assert loeuf_threshold({"loeuf": {"scale": [{"color": "#000"}]}}) is None
