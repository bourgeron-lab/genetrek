"""Venn geometry: proportional circles fit their overlaps; labels land inside.

A label point in the wrong region puts a count in the wrong place on a figure
someone may publish, and nothing on screen would look broken. So every label
is checked against the shapes it is supposed to be inside.
"""

from __future__ import annotations

import itertools
import math

import pytest

from genetrek.services.compare_layout import (
    distance_for_overlap,
    fixed_layout,
    lens_area,
    proportional_layout,
    venn_layout,
)


def _shared(layout, i: int, j: int) -> float:
    a, b = layout.shapes[i], layout.shapes[j]
    return lens_area(a.rx, b.rx, math.hypot(a.cx - b.cx, a.cy - b.cy))


class TestLens:
    def test_disjoint_and_contained_limits(self):
        assert lens_area(1, 1, 3) == 0
        assert lens_area(2, 1, 0.5) == pytest.approx(math.pi)

    @pytest.mark.parametrize("target", [0.1, 1.0, 2.5])
    def test_distance_gives_the_target_overlap(self, target):
        d = distance_for_overlap(1.0, 1.2, target)
        assert lens_area(1.0, 1.2, d) == pytest.approx(target, rel=0.01)

    def test_disjoint_sets_do_not_touch(self):
        assert distance_for_overlap(1.0, 1.0, 0) > 2.0

    def test_a_subset_stays_inside(self):
        d = distance_for_overlap(2.0, 1.0, math.pi)
        assert d + 1.0 <= 2.0


class TestProportional:
    def test_two_circles_match_exactly(self):
        layout = proportional_layout([500, 300], {(0, 1): 120})
        assert layout.proportional
        assert math.pi * layout.shapes[0].rx ** 2 == pytest.approx(500)
        assert _shared(layout, 0, 1) == pytest.approx(120, rel=0.01)

    @pytest.mark.parametrize(
        ("sizes", "overlaps"),
        [
            # hc_ndd_v7, SynGO and SFARI-1 within protein-coding genes.
            ([2178, 1787, 241], {(0, 1): 424, (0, 2): 241, (1, 2): 81}),
            ([1000, 1000, 1000], {(0, 1): 300, (0, 2): 300, (1, 2): 300}),
            ([800, 400, 100], {(0, 1): 0, (0, 2): 50, (1, 2): 0}),
            # hc_ndd_v7, SPARK and brain-enriched (GTEx v11): SPARK is a subset
            # of hc_ndd_v7, so the pairwise distances break the triangle
            # inequality and the old single start left SPARK and the brain set
            # apart, dropping their 26 shared genes from the figure.
            ([2175, 306, 565], {(0, 1): 306, (0, 2): 111, (1, 2): 26}),
        ],
    )
    def test_three_circles_fit_their_pairs(self, sizes, overlaps):
        layout = proportional_layout(sizes, overlaps)
        scale = math.pi * min(s.rx for s in layout.shapes) ** 2
        for (i, j), target in overlaps.items():
            assert abs(_shared(layout, i, j) - target) <= 0.05 * max(target, scale)

    def test_a_subset_touching_a_third_set_shows_every_region(self):
        # The UpSet of that comparison: 1,784 / 454 / 280 / 85 / 26.
        regions = {(0,): 1784, (2,): 454, (0, 1): 280, (0, 2): 85, (0, 1, 2): 26}
        layout = venn_layout(
            [2175, 306, 565], {(0, 1): 306, (0, 2): 111, (1, 2): 26}, regions
        )
        assert layout.proportional
        assert set(regions) <= set(layout.labels)
        for members, (x, y) in layout.labels.items():
            assert layout.membership(x, y) == members

    def test_an_empty_set_still_gets_a_circle(self):
        layout = proportional_layout([100, 0], {(0, 1): 0})
        assert layout.shapes[1].rx > 0


class TestFixed:
    @pytest.mark.parametrize("n", [3, 4, 5])
    def test_every_region_exists(self, n):
        layout = fixed_layout(n)
        assert not layout.proportional
        wanted = {
            combo
            for k in range(1, n + 1)
            for combo in itertools.combinations(range(n), k)
        }
        assert set(layout.labels) == wanted

    @pytest.mark.parametrize("n", [3, 4, 5])
    def test_each_label_lies_in_exactly_its_region(self, n):
        layout = fixed_layout(n)
        for members, (x, y) in layout.labels.items():
            assert layout.membership(x, y) == members


class TestDispatch:
    def test_two_and_three_are_proportional(self):
        assert venn_layout([10, 10], {(0, 1): 2}).proportional
        assert venn_layout([10, 10, 10], {}).proportional

    def test_proportional_labels_are_in_their_regions(self):
        layout = venn_layout([500, 400, 300], {(0, 1): 150, (0, 2): 100, (1, 2): 80})
        for members, (x, y) in layout.labels.items():
            assert layout.membership(x, y) == members

    def test_a_region_circles_cannot_hold_falls_back_to_fixed_circles(self):
        # Three lists whose only overlap is a 10-gene core all three share.
        # Small pairwise lenses sit at the circles' edges and never meet, so
        # the best circles have no three-way region for those 10 genes.
        sizes = [300, 300, 300]
        overlaps = {(0, 1): 10, (0, 2): 10, (1, 2): 10}
        regions = {(0,): 290, (1,): 290, (2,): 290, (0, 1, 2): 10}
        assert (0, 1, 2) not in venn_layout(sizes, overlaps).labels
        layout = venn_layout(sizes, overlaps, regions)
        assert not layout.proportional
        assert set(regions) <= set(layout.labels)

    def test_six_sets_have_no_venn(self):
        with pytest.raises(ValueError, match="4 or 5"):
            venn_layout([1] * 6, {})
