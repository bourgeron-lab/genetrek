"""The application header bar, shared by every page."""

from __future__ import annotations

from nicegui import ui

from genetrek.content import CONTENT_PAGES

_CONTACT_URL = "mailto:ghfcgenetics@pasteur.fr?subject=About%20GeneTrek"


def create_header(current: str | None = None) -> None:
    """Build the header bar in the current page.

    Args:
        current: Route of the page being rendered, so the menu can mark its own
            entry. Optional, because the main page is not one of them.

    Both children of the header matter to the layout: it is ``justify-between``
    with exactly two of them, so the links have to live in a row of their own.
    Adding a third child directly would spread title, subtitle and links evenly
    across the bar instead of pinning the links to the right.
    """
    with (
        ui.header()
        .classes("items-center justify-between text-white px-6 py-4")
        .style("background-color: #3c6382;")
    ):
        with ui.row().classes("items-center gap-4"):
            # A link rather than a label: the documentation page needs a way
            # back, and the title is where everyone looks for it.
            ui.link("GeneTrek", "/").classes(
                "text-2xl font-bold text-white no-underline hover:underline",
            )
            ui.label(
                "Association of genes with neurodevelopmental disorders",
            ).classes("text-lg")
        with ui.row().classes("items-center gap-4"):
            _documentation_menu(current)
            ui.link("Contact", _CONTACT_URL).classes("text-white hover:underline")


def _documentation_menu(current: str | None) -> None:
    """The prose pages, behind one menu.

    Three mechanics are load-bearing:

    * The trigger is a focusable row, not a ``ui.button``. QBtn's padding,
      ripple and uppercase would not match the plain text link beside it, and
      QMenu binds both click and Enter on its parent, so ``tabindex=0`` is all
      a keyboard needs.
    * The trigger cannot itself be a link. QMenu's handler is passive and
      cannot ``preventDefault``, so a link would navigate *and* open the menu.
    * The entries carry ``href`` rather than a click handler, so they are real
      anchors: middle-click opens a tab and the address is bookmarkable.
      ``ui.menu_item`` is a handler on a div and offers neither.

    The hover line is a bottom border, not ``hover:underline`` like the Contact
    link beside it. A row is a flex container and the icon blockifies into a
    flex item, so ``text-decoration`` is painted over each item's own text box
    and the 4px gap between them belongs to no item -- the underline came out
    in two pieces with a visible break. The border is always present and merely
    uncoloured until hover, so there is no one-pixel shift when it appears.
    """
    with (
        ui.row()
        .classes(
            "items-center gap-1 cursor-pointer text-white "
            "border-b border-transparent hover:border-white",
        )
        .props("tabindex=0")
    ):
        ui.label("Documentation")
        ui.icon("expand_more").classes("text-lg")
        with ui.menu().props("auto-close"):
            for page in CONTENT_PAGES:
                item = ui.item(page.nav_label).props(f'href="{page.route}"')
                if page.route == current:
                    item.classes("text-weight-bold")
