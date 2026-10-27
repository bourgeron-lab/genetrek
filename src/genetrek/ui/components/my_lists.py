"""My lists: the server half of user gene lists, and every dialog they need.

Lists live in the visitor's browser (see ``ui/js/my_lists.js``). This
component receives them when the page loads, keeps them in the tab's
``state`` for as long as the tab is open, and writes every change straight
back to the browser. Nothing here stores, caches or logs a list: when the
connection closes, the server has nothing left of it.

What it maintains in ``state``:

``user_lists``
    ``{id: UserList}`` in creation order, saved lists and visit-only ones.
``base_df``
    ``config.df`` plus one Boolean column per list, named ``mylist:<id>``.
    Everything that starts a tab's work from the whole table starts here, so
    a list is searchable, selectable as a column and usable in enrichment
    exactly as a database gene list is.
``list_labels``
    Column id -> ``name · YYYY-MM-DD HH:MM``.
``on_lists_change``
    Callbacks run after any change, for the components that offer lists.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import polars as pl
from nicegui import ui

from genetrek.services import user_lists as ul

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from genetrek.config.loader import AppConfig
    from genetrek.db.gene_index import GeneIndex

#: Shown wherever a list is created or brought in. Inline, never a modal that
#: can be dismissed for good: it is the one fact a user must not miss.
LOCAL_ONLY_NOTICE = (
    "Saved in this browser only. GeneTrek keeps no copy: clearing site data, "
    "private browsing, or Safari's 7-day clean-up will erase it. "
    "Export anything important."
)

_PRIVACY_PAGE = "/my-lists"
_TEAL = "#38ada9"
_NAVY = "#1a365d"


def _local_only_notice() -> None:
    with (
        ui.row()
        .classes("w-full items-start no-wrap gap-2 p-2 rounded")
        .style("background-color: #fff8e1; border: 1px solid #f0c36d;")
    ):
        ui.icon("info").style("color: #b7791f; font-size: 1.2rem;")
        with ui.column().classes("gap-0"):
            ui.label(LOCAL_ONLY_NOTICE).classes("text-sm").style("color: #5f4b16;")
            ui.link(
                "How My lists handles your data", _PRIVACY_PAGE, new_tab=True
            ).classes(
                "text-xs",
            )


def _timezone(name: object) -> ZoneInfo:
    try:
        return ZoneInfo(str(name)) if name else ZoneInfo("UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()[:40] or "list"


class MyLists:
    """The lists of one tab, and the dialogs that create and manage them."""

    def __init__(
        self,
        config: AppConfig,
        state: dict[str, Any],
        gene_index: GeneIndex | None,
        database_keys: frozenset[str],
    ) -> None:
        self.config = config
        self.state = state
        self.gene_index = gene_index
        self.database_keys = database_keys
        #: ``None`` disables the feature: lists are sets of primary keys.
        self.key = config.roles.primary_key
        self.tz = ZoneInfo("UTC")
        self.base = "/"
        self._pending: list[Any] = []
        self._chunks = ul.FileChunks()

        state["user_lists"] = {}
        state["base_df"] = config.df
        state["list_labels"] = {}
        state.setdefault("on_lists_change", [])
        state["describe_list_column"] = self.describe_column

        self._inject_script()
        ui.on("userListsLoaded", self._on_loaded)
        ui.on("sharedLists", self._on_shared)
        ui.on("userListFile", self._on_file)
        ui.on("userListsError", self._on_error)

        self._build_save_dialog()
        self._build_paste_dialog()
        self._build_rename_dialog()
        self._build_delete_dialog()
        self._build_share_dialog()
        self._build_shared_dialog()
        self._build_manager()

        # Deferred like the table's first push: the call needs the socket.
        ui.timer(
            0.3,
            lambda: ui.run_javascript("window.gtLists && window.gtLists.announce();"),
            once=True,
        )

    # -- plumbing ---------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self.key is not None

    @property
    def lists(self) -> list[ul.UserList]:
        return list(self.state["user_lists"].values())

    def _inject_script(self) -> None:
        script = (Path(__file__).parent.parent / "js" / "my_lists.js").read_text()
        ui.add_body_html(f"<script>\n{script}\n</script>")

    def label(self, user_list: ul.UserList) -> str:
        return ul.label(user_list, self.tz)

    def apply(self, frame: pl.DataFrame) -> pl.DataFrame:
        """``frame`` with exactly the current lists as Boolean columns."""
        stale = [c for c in frame.columns if ul.is_list_column(c)]
        if stale:
            frame = frame.drop(stale)
        if self.key is None or not self.state["user_lists"]:
            return frame
        return frame.with_columns(ul.list_columns(self.key, self.lists))

    def _changed(self) -> None:
        """Rebuild everything derived from the lists, then tell subscribers."""
        state = self.state
        state["base_df"] = self.apply(self.config.df)
        state["filtered_df"] = self.apply(state["filtered_df"])
        state["list_labels"] = {lst.column: self.label(lst) for lst in self.lists}
        current = set(state["list_labels"])
        state["selected_columns"][:] = [
            c
            for c in state["selected_columns"]
            if not ul.is_list_column(c) or c in current
        ]
        for callback in state["on_lists_change"]:
            # One failing subscriber must not leave the others stale.
            with contextlib.suppress(Exception):
                callback()
        self._render_rows.refresh()

    def _persist(self, lists: Iterable[ul.UserList]) -> None:
        records = [ul.to_storage(lst) for lst in lists if lst.saved]
        if records:
            ui.run_javascript(
                f"window.gtLists && window.gtLists.put({json.dumps(records)});",
            )

    def _forget(self, ids: Sequence[str]) -> None:
        ui.run_javascript(
            f"window.gtLists && window.gtLists.remove({json.dumps(list(ids))});",
        )

    def add(self, lists: Sequence[ul.UserList]) -> None:
        for lst in lists:
            self.state["user_lists"][lst.id] = lst
        self._persist(lists)
        self._changed()

    def save_genes(self, name: str, genes: Iterable[str], *, source: str) -> None:
        """Save a gene set made elsewhere on the page, e.g. a Compare region."""
        if self.key is None:
            return
        lst = ul.create(
            name,
            genes,
            source=source,
            release=self.config.release,
            key=self.key,
            now=self._now(),
        )
        self.add([lst])
        ui.notify(
            f"Saved “{self.label(lst)}” in this browser only. Export it to keep it.",
            type="positive",
        )

    def _replace(self, lists: Sequence[ul.UserList]) -> None:
        for lst in lists:
            self.state["user_lists"][lst.id] = lst
        self._persist(lists)
        self._changed()

    def _now(self) -> datetime:
        return datetime.now(self.tz)

    # -- browser events ---------------------------------------------------

    def _on_loaded(self, e: Any) -> None:  # noqa: ANN401
        args = e.args or {}
        self.tz = _timezone(args.get("timezone"))
        base = args.get("base")
        if isinstance(base, str) and base.startswith(("http://", "https://")):
            self.base = base
        if args.get("part", 0) == 0:
            self._pending = []
        self._pending.extend(args.get("lists") or [])
        if args.get("part", 0) < args.get("parts", 1) - 1:
            return

        loaded, bad = ul.read_store(self._pending)
        self._pending = []
        # Visit-only lists exist in this tab alone; a reload of the store
        # (another tab saved something) must not drop them.
        kept = {i: lst for i, lst in self.state["user_lists"].items() if not lst.saved}
        self.state["user_lists"] = {lst.id: lst for lst in loaded} | kept
        self._changed()

        error = args.get("error")
        if error == "damaged":
            ui.notify(
                "The lists saved in this browser could not be read.",
                type="negative",
            )
        elif error == "unavailable":
            ui.notify(
                "This browser does not let GeneTrek store lists.",
                type="warning",
            )
        if bad:
            ui.notify(
                f"{bad} saved list(s) could not be read and were skipped.",
                type="warning",
            )

    def _on_error(self, e: Any) -> None:  # noqa: ANN401
        message = (e.args or {}).get("message") or "Something went wrong."
        ui.notify(str(message), type="negative", multi_line=True)

    def _on_shared(self, e: Any) -> None:  # noqa: ANN401
        payload = str((e.args or {}).get("payload") or "")
        try:
            shared = ul.decode_share(payload)
        except ul.UserListError as exc:
            ui.notify(f"This share link cannot be opened: {exc}.", type="negative")
            self._clear_shared()
            return
        if shared.key != self.key:
            ui.notify(
                f"This link was made for a database keyed on {shared.key!r}.",
                type="negative",
            )
            self._clear_shared()
            return
        self._show_shared(shared)

    def _on_file(self, e: Any) -> None:  # noqa: ANN401
        if self.key is None:
            return
        args = e.args or {}
        name = str(args.get("name") or "list")
        try:
            text = self._chunks.add(
                str(args.get("upload") or ""),
                int(args.get("part") or 0),
                int(args.get("parts") or 1),
                str(args.get("text") or ""),
            )
        except (ul.UserListError, TypeError, ValueError) as exc:
            ui.notify(f"{name}: {exc}.", type="negative")
            return
        if text is None:
            return
        if ul.is_export_file(text):
            try:
                imported = ul.from_export_json(
                    text,
                    key=self.key,
                    release=self.config.release,
                    now=self._now(),
                )
            except ul.UserListError as exc:
                ui.notify(f"{name}: {exc}.", type="negative")
                return
            fresh = [lst for lst in imported if not self._already_have(lst)]
            if fresh:
                self.add(fresh)
            skipped = len(imported) - len(fresh)
            message = f"Imported {len(fresh)} list(s) from {name}."
            if skipped:
                message += f" {skipped} already in your lists."
            ui.notify(message, type="positive" if fresh else "info")
            return
        # Anything else is a plain list of identifiers: resolve it in the
        # paste dialog, so the user sees what matched before keeping it.
        self.open_paste(text=text, name=Path(name).stem, source="import")

    def _already_have(self, candidate: ul.UserList) -> bool:
        return any(
            lst.name == candidate.name
            and lst.created == candidate.created
            and lst.genes == candidate.genes
            for lst in self.lists
        )

    def _clear_shared(self) -> None:
        ui.run_javascript("window.gtLists && window.gtLists.clearShared();")

    # -- column info ------------------------------------------------------

    def describe_column(self, column: str) -> tuple[str, str] | None:
        """Title and markdown for a list's column-info dialog, or ``None``."""
        lst = self.state["user_lists"].get(column.removeprefix(ul.COLUMN_PREFIX))
        if not ul.is_list_column(column) or lst is None:
            return None
        lines = [
            f"**Your list** — {len(lst.genes):,} genes, created "
            f"{self.label(lst).rsplit(' · ', 1)[-1]} from {_source_text(lst.source)}.",
        ]
        absent = ul.missing(lst, self.database_keys)
        if absent:
            lines.append(f"{absent:,} of its genes are not in this database.")
        if lst.release and lst.release != self.config.release:
            lines.append(f"Made with database release {lst.release}.")
        lines.append(
            "Stored in this browser only"
            + ("" if lst.saved else " — not even that: this visit only")
            # Not a link: following it would leave the page and lose the
            # search. The header's Documentation menu has the page.
            + ". See *My lists & your data* in the Documentation menu.",
        )
        return lst.name, "\n\n".join(lines)

    # -- save results -----------------------------------------------------

    def _build_save_dialog(self) -> None:
        self._save_dialog = ui.dialog()
        with self._save_dialog, ui.card().style("width: 560px; max-width: 92vw;"):
            ui.label("Save results as a list").classes("text-lg font-bold").style(
                f"color: {_NAVY};",
            )
            self._save_count = ui.label().classes("text-sm")
            self._save_name = (
                ui.input("List name", value="Search results")
                .props("outlined dense autofocus maxlength=120")
                .classes("w-full")
            )
            ui.label(
                "The creation date and time are added to the name automatically.",
            ).classes("text-xs text-grey-7 -mt-2")
            _local_only_notice()
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=self._save_dialog.close).props("flat")
                ui.button("Save", icon="bookmark_add", on_click=self._save).style(
                    f"background-color: {_TEAL}; color: white;",
                )
            self._save_name.on("keydown.enter", self._save)

    def open_save(self) -> None:
        if self.key is None:
            return
        frame = self.state["filtered_df"]
        if frame.height == 0:
            ui.notify("There are no genes in the current results.", type="warning")
            return
        whole = frame.height == self.config.df.height
        self._save_count.set_text(
            f"{frame.height:,} gene(s) from the current results"
            + (" — that is the whole table." if whole else "."),
        )
        self._save_name.set_value("Search results")
        self._save_dialog.open()

    def _save(self) -> None:
        if self.key is None:
            return
        frame = self.state["filtered_df"]
        lst = ul.create(
            self._save_name.value,
            frame[self.key].cast(pl.String).to_list(),
            source="search",
            release=self.config.release,
            key=self.key,
            now=self._now(),
        )
        self._save_dialog.close()
        self.add([lst])
        ui.notify(f"Saved “{self.label(lst)}” in this browser.", type="positive")

    # -- paste or import identifiers ---------------------------------------

    def _build_paste_dialog(self) -> None:
        self._paste_source = "paste"
        self._paste_genes: list[str] = []
        self._paste_dialog = ui.dialog()
        with self._paste_dialog, ui.card().style("width: 680px; max-width: 92vw;"):
            ui.label("New list from genes").classes("text-lg font-bold").style(
                f"color: {_NAVY};",
            )
            self._paste_name = (
                ui.input("List name", value="Pasted list")
                .props("outlined dense maxlength=120")
                .classes("w-full")
            )
            self._paste_text = (
                ui.textarea(
                    "Gene symbols or identifiers",
                    placeholder="One per line, or separated by commas or spaces. "
                    "Symbols, HGNC, NCBI and Ensembl IDs are all accepted; "
                    "former symbols resolve to the current gene.",
                )
                .props("outlined autogrow input-style='max-height: 240px'")
                .classes("w-full")
            )
            self._paste_text.on_value_change(lambda _: self._paste_stale())
            self._paste_report = ui.markdown().classes("text-sm w-full")
            _local_only_notice()
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=self._paste_dialog.close).props("flat")
                ui.button("Check genes", icon="fact_check", on_click=self._check).props(
                    "outline",
                ).style(f"color: {_TEAL};")
                self._paste_save = ui.button(
                    "Save",
                    icon="bookmark_add",
                    on_click=self._save_pasted,
                ).style(f"background-color: {_TEAL}; color: white;")

    def open_paste(
        self,
        *,
        text: str = "",
        name: str = "Pasted list",
        source: str = "paste",
    ) -> None:
        if self.key is None:
            return
        self._paste_source = source
        self._paste_name.set_value(ul.clean_name(name) or "Pasted list")
        self._paste_text.set_value(text)
        self._paste_stale()
        self._paste_dialog.open()
        if text:
            self._check()

    def _paste_stale(self) -> None:
        self._paste_genes = []
        self._paste_save.disable()
        self._paste_report.set_content("")

    def _check(self) -> None:
        values = ul.identifiers_from_text(self._paste_text.value or "")
        if not values:
            self._paste_report.set_content("Nothing to check yet.")
            return
        if self.gene_index is None:
            self._paste_report.set_content(
                "Gene resolution is unavailable on this server.",
            )
            return
        report = self.gene_index.resolve(values)
        resolved = report.resolved()[report.key].cast(pl.String).to_list()
        self._paste_genes = resolved
        counts = report.counts
        renamed = counts.get("alias_symbol", 0) + counts.get("prev_symbol", 0)
        lines = [
            f"**{len(resolved):,} gene(s)** from {report.n_input:,} entries.",
        ]
        if renamed:
            lines.append(
                f"{renamed:,} former or alias symbol(s) mapped to the current gene."
            )
        for title, values_ in (
            ("Ambiguous, left out", report.ambiguous),
            ("Not found, left out", report.unmatched),
        ):
            if values_:
                shown = ", ".join(f"`{v}`" for v in values_[:25])
                more = f" and {len(values_) - 25:,} more" if len(values_) > 25 else ""  # noqa: PLR2004
                lines.append(f"{title} ({len(values_):,}): {shown}{more}")
        self._paste_report.set_content("\n\n".join(lines))
        if resolved:
            self._paste_save.enable()

    def _save_pasted(self) -> None:
        if self.key is None or not self._paste_genes:
            return
        lst = ul.create(
            self._paste_name.value,
            self._paste_genes,
            source=self._paste_source,
            release=self.config.release,
            key=self.key,
            now=self._now(),
        )
        self._paste_dialog.close()
        self.add([lst])
        ui.notify(f"Saved “{self.label(lst)}” in this browser.", type="positive")

    # -- rename -----------------------------------------------------------

    def _build_rename_dialog(self) -> None:
        self._rename_id = ""
        self._rename_dialog = ui.dialog()
        with self._rename_dialog, ui.card().style("width: 480px; max-width: 92vw;"):
            ui.label("Rename list").classes("text-lg font-bold").style(
                f"color: {_NAVY};",
            )
            self._rename_input = (
                ui.input("List name")
                .props("outlined dense maxlength=120")
                .classes("w-full")
            )
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=self._rename_dialog.close).props("flat")
                ui.button("Rename", on_click=self._rename).style(
                    f"background-color: {_TEAL}; color: white;",
                )
            self._rename_input.on("keydown.enter", self._rename)

    def _open_rename(self, list_id: str) -> None:
        lst = self.state["user_lists"].get(list_id)
        if lst is None:
            return
        self._rename_id = list_id
        self._rename_input.set_value(lst.name)
        self._rename_dialog.open()

    def _rename(self) -> None:
        lst = self.state["user_lists"].get(self._rename_id)
        name = ul.clean_name(self._rename_input.value)
        self._rename_dialog.close()
        if lst is None or not name or name == lst.name:
            return
        self._replace([replace(lst, name=name)])

    # -- delete -----------------------------------------------------------

    def _build_delete_dialog(self) -> None:
        self._delete_ids: list[str] = []
        self._delete_dialog = ui.dialog()
        with self._delete_dialog, ui.card().style("width: 520px; max-width: 92vw;"):
            self._delete_title = (
                ui.label()
                .classes("text-lg font-bold")
                .style(
                    f"color: {_NAVY};",
                )
            )
            ui.label(
                "Lists exist only in this browser, so a deleted list cannot be "
                "recovered unless you exported it.",
            ).classes("text-sm")
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=self._delete_dialog.close).props("flat")
                ui.button(
                    "Export, then delete",
                    icon="download",
                    on_click=lambda: self._delete(export_first=True),
                ).props("outline").style(f"color: {_TEAL};")
                ui.button(
                    "Delete",
                    icon="delete",
                    on_click=lambda: self._delete(export_first=False),
                ).props("color=negative")

    def _open_delete(self, ids: Sequence[str]) -> None:
        ids = [i for i in ids if i in self.state["user_lists"]]
        if not ids:
            return
        self._delete_ids = ids
        self._delete_title.set_text(
            f"Delete “{self.state['user_lists'][ids[0]].name}”?"
            if len(ids) == 1
            else f"Delete {len(ids)} lists?",
        )
        self._delete_dialog.open()

    def _delete(self, *, export_first: bool) -> None:
        ids = self._delete_ids
        self._delete_dialog.close()
        if export_first:
            self.export(ids)
        for list_id in ids:
            self.state["user_lists"].pop(list_id, None)
        self._selected.difference_update(ids)
        self._forget(ids)
        self._changed()
        ui.notify(f"Deleted {len(ids)} list(s).")

    # -- export -----------------------------------------------------------

    def export(self, ids: Sequence[str]) -> None:
        if self.key is None:
            return
        lists = [
            self.state["user_lists"][i] for i in ids if i in self.state["user_lists"]
        ]
        if not lists:
            return
        now = self._now()
        text = ul.to_export_json(
            lists,
            df=self.config.df,
            key=self.key,
            symbol=self.config.roles.symbol,
            release=self.config.release,
            now=now,
        )
        stem = f"genetrek-lists-{now:%Y%m%d-%H%M}"
        if len(lists) == 1:
            stem = f"genetrek-list-{_slug(lists[0].name)}-{now:%Y%m%d-%H%M}"
        ui.download.content(text, f"{stem}.json", "application/json")
        stamp = now.isoformat(timespec="seconds")
        self._replace([replace(lst, exported=stamp) for lst in lists if lst.saved])

    # -- share ------------------------------------------------------------

    def _build_share_dialog(self) -> None:
        self._share_ids: list[str] = []
        self._share_dialog = ui.dialog()
        with self._share_dialog, ui.card().style("width: 640px; max-width: 92vw;"):
            self._share_title = (
                ui.label()
                .classes("text-lg font-bold")
                .style(
                    f"color: {_NAVY};",
                )
            )
            self._share_status = ui.markdown().classes("text-sm w-full")
            self._share_link = (
                ui.input("Link").props("outlined dense readonly").classes("w-full")
            )
            ui.label(
                "Anyone with the link can see these genes. The genes travel inside "
                "the link itself, after the “#”, which browsers never send to a "
                "server: GeneTrek does not receive or store them.",
            ).classes("text-xs text-grey-7")
            with ui.row().classes("w-full justify-end"):
                ui.button("Close", on_click=self._share_dialog.close).props("flat")
                ui.button(
                    "Download file",
                    icon="download",
                    on_click=lambda: self.export(self._share_ids),
                ).props("outline").style(f"color: {_TEAL};")
                self._share_copy = ui.button(
                    "Copy link",
                    icon="link",
                    on_click=self._copy_link,
                ).style(f"background-color: {_TEAL}; color: white;")

    def _open_share(self, ids: Sequence[str]) -> None:
        if self.key is None:
            return
        lists = [
            self.state["user_lists"][i] for i in ids if i in self.state["user_lists"]
        ]
        if not lists:
            return
        self._share_ids = [lst.id for lst in lists]
        token = ul.encode_share(lists, release=self.config.release, key=self.key)
        url = ul.share_url(self.base, token)
        verdict = ul.share_class(len(url))
        genes = sum(len(lst.genes) for lst in lists)
        self._share_title.set_text(
            f"Share “{lists[0].name}”"
            if len(lists) == 1
            else f"Share {len(lists)} lists",
        )
        status = f"{genes:,} gene(s), a link of {len(url):,} characters."
        if verdict == "long":
            status += (
                "\n\n**This link is long** and may be cut or broken by some mail "
                "and chat clients. Sending the file is safer."
            )
        elif verdict == "too_long":
            status += (
                f"\n\n**Too long to share as a link** (the limit is "
                f"{ul.MAX_URL_LENGTH:,} characters). Download the file and send "
                "that instead; it imports with **Import file** in My lists."
            )
        self._share_status.set_content(status)
        offer = verdict != "too_long"
        self._share_link.set_value(url if offer else "")
        self._share_link.set_visibility(offer)
        self._share_copy.set_visibility(offer)
        self._share_dialog.open()

    def _copy_link(self) -> None:
        url = self._share_link.value
        if url:
            ui.run_javascript(
                f"window.gtLists && window.gtLists.copy({json.dumps(url)});"
            )
            ui.notify("Link copied.")

    # -- receive a shared link --------------------------------------------

    def _build_shared_dialog(self) -> None:
        self._shared_lists: list[ul.UserList] = []
        self._shared_ticks: dict[str, ui.checkbox] = {}
        self._shared_dialog = ui.dialog().props("persistent")
        with self._shared_dialog, ui.card().style("width: 640px; max-width: 92vw;"):
            ui.label("Lists shared with you").classes("text-lg font-bold").style(
                f"color: {_NAVY};",
            )
            self._shared_intro = ui.markdown().classes("text-sm w-full")
            self._shared_rows = ui.column().classes("w-full gap-1")
            _local_only_notice()
            with ui.row().classes("w-full justify-end"):
                ui.button(
                    "Discard", on_click=lambda: self._take_shared(save=None)
                ).props(
                    "flat",
                )
                ui.button(
                    "Use for this visit only",
                    on_click=lambda: self._take_shared(save=False),
                ).props("outline").style(f"color: {_TEAL};")
                ui.button(
                    "Add to my lists",
                    icon="bookmark_add",
                    on_click=lambda: self._take_shared(save=True),
                ).style(f"background-color: {_TEAL}; color: white;")

    def _show_shared(self, shared: ul.SharedLists) -> None:
        self._shared_lists = list(shared.lists)
        intro = f"This link carries {len(shared.lists)} list(s)."
        if shared.release and shared.release != self.config.release:
            intro += (
                f" They were made with database release {shared.release}; this "
                f"one is {self.config.release or 'unversioned'}."
            )
        self._shared_intro.set_content(intro)
        self._shared_rows.clear()
        self._shared_ticks = {}
        with self._shared_rows:
            for lst in shared.lists:
                absent = ul.missing(lst, self.database_keys)
                text = f"{self.label(lst)} — {len(lst.genes):,} genes"
                if absent:
                    text += f" ({absent:,} not in this database)"
                self._shared_ticks[lst.id] = ui.checkbox(text, value=True)
        self._shared_dialog.open()

    def _take_shared(self, *, save: bool | None) -> None:
        self._shared_dialog.close()
        self._clear_shared()
        if save is None:
            return
        chosen = [
            replace(lst, saved=save)
            for lst in self._shared_lists
            if self._shared_ticks.get(lst.id) is not None
            and self._shared_ticks[lst.id].value
        ]
        if chosen:
            self.add(chosen)
            where = "in this browser" if save else "for this visit only"
            ui.notify(f"Added {len(chosen)} list(s) {where}.", type="positive")

    # -- manager ----------------------------------------------------------

    def _build_manager(self) -> None:
        self._selected: set[str] = set()
        self._syncing = False
        self._batch_buttons: list[ui.button] = []
        self._row_boxes: dict[str, ui.checkbox] = {}
        self.manager = ui.dialog()
        with self.manager, ui.card().style("width: 960px; max-width: 94vw;"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("My lists").classes("text-lg font-bold").style(
                    f"color: {_NAVY};",
                )
                with ui.row().classes("gap-2"):
                    ui.button(
                        "New from genes",
                        icon="playlist_add",
                        on_click=lambda: self.open_paste(),
                    ).props("outline dense").style(f"color: {_TEAL};")
                    ui.button("Import file", icon="upload_file").props(
                        "outline dense",
                    ).style(f"color: {_TEAL};").on(
                        "click",
                        js_handler="() => window.gtLists && window.gtLists.pickFile()",
                    )
                    ui.button(
                        "Export all",
                        icon="download",
                        on_click=lambda: self.export(list(self.state["user_lists"])),
                    ).props("outline dense").style(f"color: {_TEAL};")
            _local_only_notice()
            self._render_rows()
            with ui.row().classes("w-full justify-end"):
                ui.button("Close", on_click=self.manager.close).props("flat")

    @ui.refreshable_method
    def _render_rows(self) -> None:
        lists = self.lists
        self._selected &= {lst.id for lst in lists}
        self._row_boxes = {}
        if not lists:
            ui.label(
                "No lists yet. Save the current results with “Save as list”, paste "
                "genes with “New from genes”, or import a file.",
            ).classes("text-sm italic text-grey-7 py-4")
            return

        # Ticking updates these in place rather than re-rendering the rows:
        # a re-render per tick swallowed the clicks that followed it.
        with ui.row().classes("w-full items-center gap-2"):
            self._all_box = ui.checkbox(
                on_change=lambda e: self._select_all(value=e.value),
            )
            ui.space()
            self._batch_buttons = [
                ui.button(text, icon=icon, on_click=action).props("flat dense")
                for text, icon, action in (
                    ("Export", "download", lambda: self.export(self._selection())),
                    ("Share", "share", lambda: self._open_share(self._selection())),
                    ("Delete", "delete", lambda: self._open_delete(self._selection())),
                )
            ]
        with (
            ui.scroll_area().style("height: min(56vh, 520px)"),
            ui.column().classes(
                "w-full gap-0",
            ),
        ):
            for lst in lists:
                self._render_row(lst)
        self._sync_selection()

    def _render_row(self, lst: ul.UserList) -> None:
        absent = ul.missing(lst, self.database_keys)
        with (
            ui.row()
            .classes("w-full items-center no-wrap gap-2 py-1")
            .style("border-bottom: 1px solid #e2e8f0;")
        ):
            self._row_boxes[lst.id] = ui.checkbox(
                value=lst.id in self._selected,
                on_change=lambda e, i=lst.id: self._select(i, value=e.value),
            )
            with ui.column().classes("gap-0 flex-grow"):
                ui.label(self.label(lst)).classes("text-sm font-medium")
                detail = f"{len(lst.genes):,} genes · from {_source_text(lst.source)}"
                if absent:
                    detail += f" · {absent:,} not in this database"
                ui.label(detail).classes("text-xs text-grey-7")
            if not lst.saved:
                ui.badge("this visit only", color="grey-6").props("outline")
                ui.button(
                    "Keep",
                    icon="bookmark_add",
                    on_click=lambda _, i=lst.id: self._keep(i),
                ).props("flat dense").style(f"color: {_TEAL};")
            elif lst.exported is None:
                ui.badge("not backed up", color="orange-8").props("outline").tooltip(
                    "Never exported: it exists only in this browser.",
                )
            for icon, tip, action in (
                ("edit", "Rename", lambda _, i=lst.id: self._open_rename(i)),
                ("download", "Export", lambda _, i=lst.id: self.export([i])),
                ("share", "Share", lambda _, i=lst.id: self._open_share([i])),
                ("delete", "Delete", lambda _, i=lst.id: self._open_delete([i])),
            ):
                ui.button(icon=icon, on_click=action).props("flat dense round").tooltip(
                    tip,
                )

    def _selection(self) -> list[str]:
        return [i for i in self.state["user_lists"] if i in self._selected]

    def _select(self, list_id: str, *, value: bool) -> None:
        if self._syncing:
            return
        if value:
            self._selected.add(list_id)
        else:
            self._selected.discard(list_id)
        self._sync_selection()

    def _select_all(self, *, value: bool) -> None:
        if self._syncing:
            return
        self._selected = set(self.state["user_lists"]) if value else set()
        self._sync_selection()

    def _sync_selection(self) -> None:
        """Bring the count, the boxes and the batch buttons in line.

        ``_syncing`` stops the ``set_value`` calls here from re-entering the
        handlers that called this.
        """
        if not hasattr(self, "_all_box"):
            return
        total = len(self.state["user_lists"])
        count = len(self._selected)
        self._syncing = True
        try:
            self._all_box.set_text(f"{count} of {total} selected")
            self._all_box.set_value(total > 0 and count == total)
            for list_id, box in self._row_boxes.items():
                box.set_value(list_id in self._selected)
        finally:
            self._syncing = False
        for button in self._batch_buttons:
            button.set_enabled(count > 0)

    def _keep(self, list_id: str) -> None:
        lst = self.state["user_lists"].get(list_id)
        if lst is not None:
            self._replace([replace(lst, saved=True)])

    def open_manager(self) -> None:
        self._render_rows.refresh()
        self.manager.open()


def _source_text(source: str) -> str:
    return {
        "search": "search results",
        "paste": "pasted genes",
        "import": "an imported file",
        "shared": "a shared link",
        "compare": "a comparison region",
    }.get(source, source)
