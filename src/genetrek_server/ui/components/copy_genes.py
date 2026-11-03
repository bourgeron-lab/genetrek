"""The "Copy genes" buttons: the current results, a gene list, one of My lists.

Every button copies through one :class:`GeneCopier`, which owns the format
(:func:`genetrek_server.services.gene_copy.gene_text`) and the one fallback dialog.

The copy is written by the browser after a round trip to the server, and
Safari only lets a page write the clipboard during the click itself. So the
browser reports whether the write succeeded, and when it did not, the genes
open in a dialog, selected, for the visitor to copy by hand -- rather than a
"Copied" that copied nothing.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from nicegui import ui

from genetrek_server.services.gene_copy import copy_roles, gene_text

if TYPE_CHECKING:
    from collections.abc import Callable

    import polars as pl

    from genetrek_server.config.loader import AppConfig

_TEAL = "#38ada9"
_NAVY = "#1a365d"

#: Seconds the browser has to answer whether it wrote the clipboard. The write
#: itself is immediate; this only bounds a tab that has gone away.
_ANSWER_SECONDS = 5.0

#: Resolves to whether the write happened; never rejects, so a refusal comes
#: back as ``false`` instead of as a timeout.
_WRITE_JS = (
    "navigator.clipboard"
    " ? navigator.clipboard.writeText({text}).then(() => true, () => false)"
    " : false"
)


class GeneCopier:
    """Copies a frame's genes as one identifier per line."""

    def __init__(self, config: AppConfig) -> None:
        self.roles = config.roles
        #: ``(role, label)`` pairs this database can copy, default first.
        self.choices = copy_roles(config.roles)
        self._labels = dict(self.choices)
        self._build_fallback()

    @property
    def enabled(self) -> bool:
        return bool(self.choices)

    def _build_fallback(self) -> None:
        self._fallback = ui.dialog()
        with self._fallback, ui.card().style("width: 560px; max-width: 92vw;"):
            self._fallback_title = (
                ui.label().classes("text-lg font-bold").style(f"color: {_NAVY};")
            )
            ui.label(
                "This browser did not let GeneTrek write to the clipboard. The "
                "genes are selected below: copy them with Ctrl+C (⌘C on a Mac).",
            ).classes("text-sm")
            self._fallback_text = (
                ui.textarea().props("readonly outlined autogrow").classes("w-full")
            ).style("max-height: 50vh; overflow-y: auto;")
            with ui.row().classes("w-full justify-end"):
                ui.button("Close", on_click=self._fallback.close).props("flat")
        # Selected once the dialog has finished opening: before that, Quasar's
        # own focus handling takes the focus (and the selection) back.
        self._fallback.on(
            "show",
            js_handler=(
                f"() => {{ const e = getHtmlElement({self._fallback_text.id});"
                " const t = e?.matches('textarea') ? e : e?.querySelector('textarea');"
                " if (t) { t.focus(); t.select(); } }"
            ),
        )

    def _show_fallback(self, text: str, title: str) -> None:
        self._fallback_title.set_text(title)
        self._fallback_text.set_value(text)
        self._fallback.open()

    async def copy(self, frame: pl.DataFrame, role: str | None = None) -> None:
        """Put ``frame``'s genes on the clipboard as ``role`` (default: the first)."""
        if not self.choices:
            return
        role = role or self.choices[0][0]
        result = gene_text(frame, self.roles, role)
        what = self._labels[role]
        if not result.copied:
            ui.notify(f"No {what} to copy.", type="warning")
            return
        title = f"{result.copied:,} {what}"
        if result.without_id:
            title += f" ({result.without_id:,} genes have none)"
        try:
            written = await ui.run_javascript(
                _WRITE_JS.format(text=json.dumps(result.text)),
                timeout=_ANSWER_SECONDS,
            )
        except TimeoutError:
            written = False
        if written is True:
            ui.notify(f"Copied {title}.", type="positive")
        else:
            self._show_fallback(result.text, title)

    def results_button(self, frame: Callable[[], pl.DataFrame]) -> ui.dropdown_button:
        """A split button: the main part copies symbols, the arrow other ids."""
        with (
            ui.dropdown_button(
                "Copy genes",
                icon="content_copy",
                split=True,
                auto_close=True,
                on_click=lambda: self.copy(frame()),
            )
            .props("outline")
            .style(f"color: {_TEAL};") as button
        ):
            self._menu_items(frame)
        return button

    def icon_button(self, frame: Callable[[], pl.DataFrame]) -> ui.button:
        """A round icon whose menu picks the identifier, for a list's row."""
        with ui.button(icon="content_copy").props("flat dense round") as button:
            ui.tooltip("Copy genes")
            with ui.menu():
                self._menu_items(frame)
        return button

    def _menu_items(self, frame: Callable[[], pl.DataFrame]) -> None:
        for role, label in self.choices:
            ui.item(
                f"Copy {label}",
                on_click=lambda _, r=role: self.copy(frame(), r),
            )
