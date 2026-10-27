"""The deferred-plot state machine.

The two figures in the statistics drawer carry a point per gene, so whether
they are built at page construction or on first sight is the difference between
a 2.6 MB page and a 97 KB one. That decision is a small state machine, and
these tests pin it: nothing is drawn until the drawer shows it, a search behind
a closed drawer does no work, and the redraw it deferred happens on the next
open.

The draw is also *scheduled* rather than immediate, which is not a detail. The
toggle button tracks the drawer for a fixed wall-clock window, so a figure that
blocks the main thread mid-slide left the button stranded at the viewport edge
until the drawer was closed and reopened. The scheduler is injected here so the
timing is a contract rather than a race.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from genetrek.ui.components.stats_drawer import _DRAW_DELAY, _DeferredPlot

if TYPE_CHECKING:
    from collections.abc import Callable

#: Quasar's drawer slide, in seconds. `_DRAW_DELAY` must clear it.
_SLIDE_SECONDS = 0.35


class FakeScheduler:
    """Stands in for ``ui.timer``, running callbacks when the test says so."""

    def __init__(self) -> None:
        self.pending: list[tuple[float, Callable[[], None]]] = []

    def __call__(self, delay: float, callback: Callable[[], None]) -> None:
        self.pending.append((delay, callback))

    def run(self) -> None:
        """Fire everything currently scheduled."""
        due, self.pending = self.pending, []
        for _, callback in due:
            callback()


class TestDeferredPlot:
    def plot(self) -> tuple[_DeferredPlot, list[int], FakeScheduler]:
        calls: list[int] = []
        clock = FakeScheduler()
        return _DeferredPlot(lambda: calls.append(1), clock), calls, clock

    def test_nothing_is_drawn_at_construction(self):
        """The whole point: a closed drawer costs the page nothing."""
        _, calls, clock = self.plot()
        assert calls == []
        assert clock.pending == []

    def test_the_first_open_schedules_rather_than_draws(self):
        """Drawing inside the slide is what stranded the toggle button."""
        plot, calls, clock = self.plot()
        plot.set_showing(showing=True)
        assert calls == []
        assert len(clock.pending) == 1

    def test_the_delay_outlasts_the_drawer_slide(self):
        assert _DRAW_DELAY > _SLIDE_SECONDS

    def test_the_first_open_draws_once_the_delay_elapses(self):
        plot, calls, clock = self.plot()
        plot.set_showing(showing=True)
        clock.run()
        assert len(calls) == 1

    def test_reopening_without_a_change_does_not_redraw(self):
        plot, calls, clock = self.plot()
        plot.set_showing(showing=True)
        clock.run()
        plot.set_showing(showing=False)
        plot.set_showing(showing=True)
        clock.run()
        assert len(calls) == 1

    def test_closing_before_the_delay_elapses_drops_the_draw(self):
        """Opening and closing again quickly should not build anything."""
        plot, calls, clock = self.plot()
        plot.set_showing(showing=True)
        plot.set_showing(showing=False)
        clock.run()
        assert calls == []

    def test_a_search_behind_a_closed_drawer_does_no_work(self):
        """A figure nobody is looking at used to be rebuilt on every search."""
        plot, calls, clock = self.plot()
        plot.invalidate()
        plot.invalidate()
        clock.run()
        assert calls == []

    def test_the_deferred_redraw_happens_on_the_next_open(self):
        plot, calls, clock = self.plot()
        plot.invalidate()
        plot.set_showing(showing=True)
        clock.run()
        assert len(calls) == 1

    def test_a_search_while_open_redraws_immediately(self):
        """No slide is in progress, so there is nothing to schedule around."""
        plot, calls, clock = self.plot()
        plot.set_showing(showing=True)
        clock.run()
        plot.invalidate()
        assert len(calls) == 2

    def test_several_searches_behind_a_closed_drawer_collapse_into_one_redraw(self):
        plot, calls, clock = self.plot()
        plot.set_showing(showing=True)
        clock.run()
        plot.set_showing(showing=False)
        plot.invalidate()
        plot.invalidate()
        plot.invalidate()
        plot.set_showing(showing=True)
        clock.run()
        assert len(calls) == 2
