"""Venn diagram geometry: where each set's shape goes, and each region's label.

Two kinds of layout:

* **2-3 sets, area-proportional circles** (an Euler diagram). Each circle's
  area is its set size. Two circles are placed exactly: the distance giving the
  target overlap area is found by bisection on the closed-form lens area. Three
  are placed by minimising the squared error of the three pairwise overlaps --
  the approach venn.js takes -- from several starting arrangements, keeping the
  best. One start is not enough: the pairwise distances often violate the
  triangle inequality (a subset, plus a list that touches both), which lays the
  start out in a line, and an overlap's area is flat in the distance while two
  circles are apart or nested, so the search can stop with two circles that
  should overlap not touching. The loss adds a pull for exactly those plateaus.
  The three-way area is not fitted; with three circles it generally cannot be,
  so the counts, not the areas, are authoritative.
* **Fixed shapes when circles cannot show the counts**: a region with genes in
  it must have area to carry its count. When the best circles leave one out,
  three sets fall back to the classic symmetric layout rather than drop it.
* **4-5 sets, fixed ellipses**. No arrangement of four or five convex shapes
  makes every area proportional, so these are the classic layouts in which every
  one of the 15 or 31 regions exists, and the figure says the areas mean
  nothing.

Region label points are found the same way for both: rasterise the layout,
then take, for each membership signature, the grid point deepest inside its
region (a distance transform). That makes a label land inside its region by
construction rather than by hand-tuned coordinates, which is what
``tests/test_compare_layout.py`` checks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np
from scipy import ndimage, optimize

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: Grid resolution for label placement, per axis.
_GRID = 360
#: Gap left between two circles that share no gene, as a fraction of the
#: smaller radius: tangent circles read as touching, i.e. overlapping.
_DISJOINT_GAP = 0.15


@dataclass(frozen=True)
class Shape:
    """A circle (``rx == ry``) or a rotated ellipse."""

    cx: float
    cy: float
    rx: float
    ry: float
    angle: float = 0.0  # degrees, anticlockwise

    def contains(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        theta = math.radians(self.angle)
        dx, dy = x - self.cx, y - self.cy
        u = dx * math.cos(theta) + dy * math.sin(theta)
        v = -dx * math.sin(theta) + dy * math.cos(theta)
        rx, ry = max(self.rx, 1e-12), max(self.ry, 1e-12)
        result: np.ndarray = (u / rx) ** 2 + (v / ry) ** 2 <= 1.0
        return result

    def outline(self, points: int = 96) -> list[tuple[float, float]]:
        theta = math.radians(self.angle)
        out = []
        for k in range(points):
            t = 2 * math.pi * k / points
            u, v = self.rx * math.cos(t), self.ry * math.sin(t)
            out.append(
                (
                    self.cx + u * math.cos(theta) - v * math.sin(theta),
                    self.cy + u * math.sin(theta) + v * math.cos(theta),
                ),
            )
        return out

    def svg_path(self, points: int = 96) -> str:
        """A closed SVG path, which is how Plotly draws a rotated ellipse."""
        coords = self.outline(points)
        head = f"M {coords[0][0]:.5f},{coords[0][1]:.5f}"
        body = " ".join(f"L {x:.5f},{y:.5f}" for x, y in coords[1:])
        return f"{head} {body} Z"


@dataclass(frozen=True)
class Layout:
    shapes: tuple[Shape, ...]
    #: Membership signature -> label point, for every region with any area.
    labels: Mapping[tuple[int, ...], tuple[float, float]]
    bounds: tuple[float, float, float, float]  # xmin, xmax, ymin, ymax
    proportional: bool
    #: Membership signature -> how far the label point is from the region's
    #: edge, as a fraction of the drawing's span. A region too shallow for its
    #: count is labelled from outside, with a leader line.
    room: Mapping[tuple[int, ...], float] = field(default_factory=dict)

    def membership(self, x: float, y: float) -> tuple[int, ...]:
        px, py = np.array([x]), np.array([y])
        return tuple(
            i for i, s in enumerate(self.shapes) if bool(s.contains(px, py)[0])
        )


# -- two circles ----------------------------------------------------------------


def lens_area(r1: float, r2: float, d: float) -> float:
    """Area shared by two circles of radii ``r1``, ``r2`` whose centres are ``d`` apart."""
    if d >= r1 + r2:
        return 0.0
    if d <= abs(r1 - r2):
        return math.pi * min(r1, r2) ** 2
    a1 = r1**2 * math.acos((d * d + r1 * r1 - r2 * r2) / (2 * d * r1))
    a2 = r2**2 * math.acos((d * d + r2 * r2 - r1 * r1) / (2 * d * r2))
    k = (-d + r1 + r2) * (d + r1 - r2) * (d - r1 + r2) * (d + r1 + r2)
    return a1 + a2 - 0.5 * math.sqrt(max(k, 0.0))


def distance_for_overlap(r1: float, r2: float, overlap: float) -> float:
    """Centre distance at which two circles share ``overlap`` area."""
    small = min(r1, r2)
    if overlap <= 0:
        return r1 + r2 + _DISJOINT_GAP * small
    if overlap >= math.pi * small**2 * (1 - 1e-9):
        # One set inside the other: concentric would hide the smaller circle's
        # edge against the larger's, so keep it inside but off-centre.
        return max(abs(r1 - r2) * 0.5, 0.0)
    lo, hi = abs(r1 - r2), r1 + r2
    for _ in range(80):
        mid = (lo + hi) / 2
        if lens_area(r1, r2, mid) > overlap:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _radius(size: int, floor: float) -> float:
    return max(math.sqrt(size / math.pi), floor)


def _pair_loss(ri: float, rj: float, d: float, want: float, scale: float) -> float:
    """Squared overlap error for one pair, plus a pull wherever the area is flat.

    Apart, the shared area is 0 at every distance; nested, it is the smaller
    circle at every distance. A pair stranded on either plateau with the wrong
    overlap gets no signal from the area alone, so the distance to the plateau's
    edge stands in for it there. Sets that share nothing are kept a gap apart.
    """
    small = min(ri, rj)
    loss = ((lens_area(ri, rj, d) - want) / scale) ** 2
    if want <= 0:
        gap = ri + rj + _DISJOINT_GAP * small - d
        if gap > 0:
            loss += (gap / small) ** 2
    elif d > ri + rj:
        loss += ((d - ri - rj) / small) ** 2
    elif want < math.pi * small**2 * (1 - 1e-9) and d < abs(ri - rj):
        loss += ((abs(ri - rj) - d) / small) ** 2
    return loss


def _fit_three(
    radii: Sequence[float],
    target: Mapping[tuple[int, int], float],
    wanted: Mapping[tuple[int, int], float],
) -> list[tuple[float, float]]:
    """Centres for three circles whose pairwise overlaps best match ``wanted``.

    Circle 0 sits at the origin and circle 1 on the x axis, which leaves three
    free coordinates and no rotation or translation for the search to wander
    along. Starts: circle 1 at its target distance, circle 2 at its target
    distance from circle 0 at seven angles plus the triangle's own.
    """
    scale = max(math.pi * min(radii) ** 2, 1.0)

    def centres_of(p: np.ndarray) -> list[tuple[float, float]]:
        return [(0.0, 0.0), (float(p[0]), 0.0), (float(p[1]), float(p[2]))]

    def loss(p: np.ndarray) -> float:
        pts = centres_of(p)
        return sum(
            _pair_loss(
                radii[i],
                radii[j],
                math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]),
                area,
                scale,
            )
            for (i, j), area in wanted.items()
        )

    d01, d02, d12 = target[(0, 1)], target[(0, 2)], target[(1, 2)]
    cos_angle = (d01**2 + d02**2 - d12**2) / (2 * d01 * d02) if d01 and d02 else 0.0
    angles = [math.acos(min(1.0, max(-1.0, cos_angle)))]
    angles += [k * math.pi / 6 for k in range(7)]
    # A step the size of the circles: the default simplex steps 5% of each
    # coordinate, which is nothing on a coordinate that starts at zero.
    step = 0.5 * sum(radii) / 3
    best: tuple[float, np.ndarray] | None = None
    for angle in angles:
        start = np.array([d01, d02 * math.cos(angle), d02 * math.sin(angle)])
        simplex = np.vstack([start, start + step * np.eye(3)])
        fitted = optimize.minimize(
            loss,
            start,
            method="Nelder-Mead",
            options={
                "initial_simplex": simplex,
                "xatol": 1e-6,
                "fatol": 1e-12,
                "maxiter": 4000,
            },
        )
        if best is None or fitted.fun < best[0]:
            best = (float(fitted.fun), fitted.x)
    assert best is not None  # noqa: S101 -- angles is never empty
    return centres_of(best[1])


def proportional_layout(
    sizes: Sequence[int],
    overlaps: Mapping[tuple[int, int], int],
) -> Layout:
    """Area-proportional circles for two or three sets.

    ``overlaps`` maps each pair ``(i, j)`` with ``i < j`` to its shared count.
    """
    n = len(sizes)
    if n not in (2, 3):
        msg = f"a proportional layout takes 2 or 3 sets, not {n}"
        raise ValueError(msg)
    biggest = max(*sizes, 1)
    # An empty set still needs a visible (tiny) circle to carry its name.
    floor = 0.02 * math.sqrt(biggest / math.pi)
    radii = [_radius(s, floor) for s in sizes]
    target = {
        pair: distance_for_overlap(
            radii[pair[0]], radii[pair[1]], overlaps.get(pair, 0)
        )
        for pair in ((0, 1), (0, 2), (1, 2))
        if pair[1] < n
    }

    centres: list[tuple[float, float]] = [(0.0, 0.0), (target[(0, 1)], 0.0)]
    if n == 3:  # noqa: PLR2004
        wanted = {pair: float(overlaps.get(pair, 0)) for pair in target}
        centres = _fit_three(radii, target, wanted)

    # Centre the drawing on the origin.
    mx = sum(c[0] for c in centres) / n
    my = sum(c[1] for c in centres) / n
    shapes = tuple(
        Shape(cx - mx, cy - my, r, r)
        for (cx, cy), r in zip(centres, radii, strict=True)
    )
    return _finish(shapes, proportional=True)


# -- four and five ellipses -------------------------------------------------------

#: Classic symmetric layouts in which every region exists: (cx, cy, rx, ry, angle).
_FIXED: dict[int, tuple[tuple[float, float, float, float, float], ...]] = {
    3: (
        (0.350, 0.600, 0.300, 0.300, 0.0),
        (0.650, 0.600, 0.300, 0.300, 0.0),
        (0.500, 0.340, 0.300, 0.300, 0.0),
    ),
    4: (
        (0.350, 0.400, 0.360, 0.225, 140.0),
        (0.450, 0.500, 0.360, 0.225, 140.0),
        (0.544, 0.500, 0.360, 0.225, 40.0),
        (0.644, 0.400, 0.360, 0.225, 40.0),
    ),
    5: (
        (0.428, 0.449, 0.435, 0.250, 155.0),
        (0.469, 0.543, 0.435, 0.250, 82.0),
        (0.558, 0.523, 0.435, 0.250, 10.0),
        (0.578, 0.432, 0.435, 0.250, 118.0),
        (0.489, 0.383, 0.435, 0.250, 46.0),
    ),
}


@lru_cache(maxsize=4)
def fixed_layout(n: int) -> Layout:
    """The classic layout for three to five sets. Areas are not proportional."""
    if n not in _FIXED:
        msg = f"a fixed layout exists for 3, 4 or 5 sets, not {n}"
        raise ValueError(msg)
    shapes = tuple(Shape(*params) for params in _FIXED[n])
    return _finish(shapes, proportional=False)


def venn_layout(
    sizes: Sequence[int],
    overlaps: Mapping[tuple[int, int], int],
    regions: Mapping[tuple[int, ...], int] | None = None,
) -> Layout:
    """The right layout for 2-5 sets.

    ``regions`` maps each membership signature to its gene count. Given, the
    proportional layout is kept only if every non-empty region has area to carry
    its count; otherwise three sets fall back to the fixed layout, in which every
    region exists. A count the figure has no place for is a count it would drop.
    """
    return _venn_layout(
        tuple(sizes),
        tuple(sorted(overlaps.items())),
        frozenset(m for m, size in (regions or {}).items() if size),
    )


@lru_cache(maxsize=32)
def _venn_layout(
    sizes: tuple[int, ...],
    overlaps: tuple[tuple[tuple[int, int], int], ...],
    nonempty: frozenset[tuple[int, ...]],
) -> Layout:
    if len(sizes) > 3:  # noqa: PLR2004
        return fixed_layout(len(sizes))
    layout = proportional_layout(sizes, dict(overlaps))
    if nonempty <= layout.labels.keys() or len(sizes) not in _FIXED:
        return layout
    return fixed_layout(len(sizes))


# -- labels -----------------------------------------------------------------------


def _finish(shapes: tuple[Shape, ...], *, proportional: bool) -> Layout:
    xs = [x for s in shapes for x, _ in s.outline(48)]
    ys = [y for s in shapes for _, y in s.outline(48)]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    pad = 0.04 * span
    bounds = (min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad)

    gx = np.linspace(bounds[0], bounds[1], _GRID)
    gy = np.linspace(bounds[2], bounds[3], _GRID)
    x, y = np.meshgrid(gx, gy)
    inside = np.stack([s.contains(x, y) for s in shapes])
    code = np.zeros(x.shape, dtype=np.int64)
    for i in range(len(shapes)):
        code |= inside[i].astype(np.int64) << i

    labels: dict[tuple[int, ...], tuple[float, float]] = {}
    room: dict[tuple[int, ...], float] = {}
    cell = (bounds[1] - bounds[0]) / (_GRID - 1)
    for value in np.unique(code):
        if value == 0:
            continue
        mask = code == value
        depth = ndimage.distance_transform_edt(mask)  # type: ignore[attr-defined]
        row, col = np.unravel_index(int(np.argmax(depth)), depth.shape)
        members = tuple(i for i in range(len(shapes)) if value >> i & 1)
        labels[members] = (float(gx[col]), float(gy[row]))
        room[members] = float(depth[row, col]) * cell / span

    return Layout(shapes, labels, bounds, proportional, room)
