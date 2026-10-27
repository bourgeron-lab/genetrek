"""The prose pages, declared once.

At the package root rather than under ``ui/`` because ``config/loader.py`` and
``paths.py`` both need this table, and neither may import ``genetrek.ui`` --
``db/`` and the CLI would drag NiceGUI in behind them.

Adding a page means adding an entry here and writing the markdown file. Nothing
else: the loader reads every declared slug, the registrar builds a page per
entry, and the header builds its menu from the same tuple.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContentPage:
    """One markdown-backed page.

    Attributes:
        route: The URL path. Must not start with ``/api``, which is the API
            router's prefix, and must not be ``/docs``, which is the generated
            OpenAPI reference.
        slug: Names the file, ``resources/content/<slug>.md``.
        title: Browser title, shown after ``GeneTrek — ``.
        nav_label: The entry in the header menu.
        wide: Whether the page's card spans a wider measure than prose wants.
            For a page whose computed section is a table, such as the column
            reference, which is unreadable at the prose width.
    """

    route: str
    slug: str
    title: str
    nav_label: str
    wide: bool = False


#: The prose pages, in the order the header menu lists them.
#:
#: ``/documentation`` stays the general page so the URL that has been published
#: keeps working and keeps meaning "start here".
CONTENT_PAGES: tuple[ContentPage, ...] = (
    ContentPage(
        route="/documentation",
        slug="documentation",
        title="Documentation",
        nav_label="General documentation",
    ),
    ContentPage(
        route="/columns",
        slug="columns",
        title="Column reference",
        nav_label="Column reference",
        wide=True,
    ),
    ContentPage(
        route="/my-lists",
        slug="my-lists",
        title="My lists",
        nav_label="My lists & your data",
    ),
    ContentPage(
        route="/api-reference",
        slug="api-reference",
        title="API reference",
        nav_label="API reference",
    ),
    ContentPage(
        route="/changelog",
        slug="changelog",
        title="Changelog",
        nav_label="Changelog",
    ),
    ContentPage(
        route="/cite",
        slug="cite",
        title="How to cite",
        nav_label="How to cite",
    ),
)
