"""Dialog describing one column, opened by clicking its header label.

The header's native ``title`` tooltip stays for a quick hover peek. This is the
version that can carry formatting and working links -- a DOI in particular is
only useful if you can follow it, which a tooltip cannot offer.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from nicegui import ui

from genetrek.services.column_reference import doi_url, format_date
from genetrek.ui.markdown import MARKDOWN_EXTRAS

if TYPE_CHECKING:
    from collections.abc import Callable

    from genetrek.config.loader import AppConfig


#: A bare URL in free-text metadata. The lookbehind is what keeps this from
#: touching a URL that is already a link: in ``[text](url)`` and ``<url>`` the
#: character before the scheme is one of these, and re-wrapping it would emit
#: ``[text]([url](url))``. Brackets and parentheses are excluded from the URL
#: itself for the same reason -- a trailing ``)`` in the prose is punctuation,
#: not part of the address.
_BARE_URL_RE = re.compile(r"(?<![(\[<])\bhttps?://[^\s<>()\[\]]+")

#: Punctuation that ends a sentence rather than a URL. A source line reading
#: "... see https://example.org/page." means the full stop is prose.
_URL_TRAILING = ".,;:!?"


def _linkify(value: str) -> str:
    """Make bare URLs in free text followable.

    ``source`` and ``description`` are prose fields, and people write URLs into
    them plainly -- "PanelApp Australia - https://panelapp-aus.org/panels/51/
    v0.248". The dialog renders markdown with ``target-blank-links``, so once a
    URL is a link it opens in a new tab like the DOI does, which matters
    because this is a single-page app: a same-tab navigation would discard the
    table's filters, sort and pagination.
    """

    def wrap(match: re.Match[str]) -> str:
        url = match.group(0).rstrip(_URL_TRAILING)
        return f"[{url}]({url}){match.group(0)[len(url) :]}"

    return _BARE_URL_RE.sub(wrap, value)


def _format_doi(value: str) -> str:
    """Render a DOI as a followable markdown link."""
    return f"[{value}]({doi_url(value)})"


def _format_type(meta: dict[str, Any]) -> str:
    """Value type, qualified by genome assembly for coordinate columns."""
    value_type = str(meta.get("value_type") or "")
    assembly = str(meta.get("assembly") or "")
    if value_type and assembly:
        return f"{value_type} ({assembly})"
    return value_type or assembly


def _format_derivation(meta: dict[str, Any]) -> str:
    """Describe a derived column as the rule it is, not as imported data."""
    op = str(meta.get("derived_op") or "")
    sources = meta.get("derived_from") or []
    if not op or not sources:
        return ""
    listed = ", ".join(f"`{name}`" for name in sources)
    if op == "external":
        return f"computed from {listed} outside GeneTrek (rule in the description)"
    return f"{op} of {listed}"


def _format_universe(
    meta: dict[str, Any],
    n_measured: int | None,
    n_rows: int | None,
) -> str:
    """Which genes the column measured, and how many -- when that is not all."""
    text = str(meta.get("universe") or "").strip()
    partial = n_measured is not None and n_rows is not None and n_measured < n_rows
    count = f"{n_measured:,} of {n_rows:,} genes measured" if partial else ""
    if text and count:
        return f'{text}. {count}; the others are not measured, not "no"'
    return text or count


def _format_group(meta: dict[str, Any]) -> str:
    """Render the display group as ``category / subcategory``.

    A subcategory with no category is dropped rather than shown alone: it has
    no group to sit in, which ``genetrek-db validate`` warns about.
    """
    category = str(meta.get("category") or "").strip()
    if not category:
        return ""
    subcategory = str(meta.get("subcategory") or "").strip()
    return f"{category} / {subcategory}" if subcategory else category


def column_tooltip(meta: dict[str, Any]) -> str:
    """Build the plain-text hover tooltip for one column, one fact per line.

    Shared by the table header and the column selector so the two cannot drift:
    hovering a column says the same thing wherever you hover it. This is the
    quick peek; :func:`column_info_markdown` is the formatted version with a
    followable DOI, which a native ``title`` cannot offer.

    Args:
        meta: That column's ``FieldMeta`` dump from ``config.display_config``.

    Returns:
        A newline-separated string, headed by the column's label.
    """
    parts = [str(meta.get("fullname") or "")]
    facts: list[tuple[str, str]] = [
        ("Description", str(meta.get("description") or "")),
        ("Updated", format_date(str(meta.get("update") or ""))),
        # Plain text: a native `title` would show markdown as markdown.
        ("Source", str(meta.get("source") or "")),
        ("DOI", str(meta.get("doi") or "")),
        ("Licence", str(meta.get("licence") or "")),
        ("Universe", str(meta.get("universe") or "")),
    ]
    parts += [f"{label}: {value}" for label, value in facts if value]
    return "\n".join(part for part in parts if part)


def column_info_markdown(
    column: str,
    meta: dict[str, Any],
    *,
    n_measured: int | None = None,
    n_rows: int | None = None,
) -> str:
    """Build the dialog body for one column's metadata.

    Kept a pure function so it can be tested without a browser: it is the only
    part of this component with any logic in it.

    Args:
        column: The column name, which is also its key in the parquet.
        meta: That column's ``FieldMeta`` dump from ``config.display_config``.
        n_measured: Genes with a value in the column -- its universe's size.
        n_rows: Genes in the table.

    Returns:
        Markdown, for rendering with :data:`MARKDOWN_EXTRAS`.
    """
    description = _linkify(str(meta.get("description") or "").strip())
    blocks = [description or "*No description recorded for this column.*"]

    # Two trailing spaces is a markdown line break, which keeps these as one
    # tight block rather than a paragraph each.
    facts: list[tuple[str, str]] = [
        ("Group", _format_group(meta)),
        ("Source", _linkify(str(meta.get("source") or ""))),
        ("Updated", format_date(str(meta.get("update") or ""))),
        ("DOI", _format_doi(str(meta["doi"])) if meta.get("doi") else ""),
        ("Licence", str(meta.get("licence") or "")),
        ("Type", _format_type(meta)),
        ("Derived", _format_derivation(meta)),
        ("Universe", _format_universe(meta, n_measured, n_rows)),
    ]
    listed = [f"**{label}:** {value}" for label, value in facts if value]
    if listed:
        blocks.append("  \n".join(listed))

    blocks.append(f"Column name in the data: `{column}`")
    return "\n\n".join(blocks)


def create_column_info_dialog(
    config: AppConfig,
    describe_extra: Callable[[str], tuple[str, str] | None] | None = None,
) -> tuple[ui.dialog, Callable[[str], None]]:
    """Build the column-info dialog once, and return it with its opener.

    ``describe_extra`` answers for columns the metadata does not know -- the
    visitor's own lists -- with a title and markdown, or ``None``.

    One dialog is reused for every column: a ``ui.dialog`` is an element that is
    merely hidden on close, so building a fresh one per click would leave an
    element tree per click behind for the lifetime of the page. Only the title
    and body content change, so nothing is torn down and rebuilt either.

    Returns:
        The dialog, and a ``show(column)`` that fills and opens it.
    """
    dialog = ui.dialog()
    with dialog, ui.card().style("width: 640px; max-width: 90vw;"):
        title = ui.label().classes("text-lg font-bold").style("color: #1a365d;")
        body = ui.markdown(extras=MARKDOWN_EXTRAS).classes("text-sm")
        with ui.row().classes("w-full justify-end"):
            ui.button("Close", on_click=dialog.close).props("flat")

    def show(column: str) -> None:
        # The column name arrives from the browser, so it is checked rather than
        # trusted: an unknown one means a stale client, and does nothing.
        meta = config.display_config.get(column)
        if meta is None:
            extra = describe_extra(column) if describe_extra else None
            if extra is None:
                return
            title.set_text(extra[0])
            body.set_content(extra[1])
            dialog.open()
            return
        title.set_text(str(meta.get("fullname") or column))
        frame = config.df
        n_measured = (
            frame.height - frame[column].null_count()
            if column in frame.columns
            else None
        )
        body.set_content(
            column_info_markdown(
                column,
                meta,
                n_measured=n_measured,
                n_rows=frame.height,
            ),
        )
        dialog.open()

    return dialog, show
