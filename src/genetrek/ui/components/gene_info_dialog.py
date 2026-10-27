"""Dialog describing one gene, opened from its symbol or the row's info button.

What the table cannot show: where the gene is in *both* assemblies at once, and
somewhere to go next. The genome-browser links are built here rather than in
``external_links.yaml`` because they need per-assembly logic -- UCSC's own name
for an assembly, a ``chr`` prefix our data does not store, the track hub -- that
a ``{placeholder}`` template cannot express.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from nicegui import ui

from genetrek.services import lookup
from genetrek.ui.markdown import MARKDOWN_EXTRAS

if TYPE_CHECKING:
    from collections.abc import Callable

    from genetrek.config.loader import AppConfig
    from genetrek.schema import LocusColumns, Roles

#: UCSC's name for an assembly. Not derivable from the metadata: this is UCSC's
#: naming of the same genome, not ours. An assembly absent here simply gets no
#: UCSC link rather than a guessed one.
_UCSC_DB: dict[str, str] = {"GRCh38": "hg38", "GRCh37": "hg19"}

#: Ensembl serves the older assembly from a separate archive host.
_ENSEMBL_HOST: dict[str, str] = {
    "GRCh38": "www.ensembl.org",
    "GRCh37": "grch37.ensembl.org",
}

#: The lab's own track hub, added to the UCSC link so a gene opens alongside the
#: NDD-association and autism-OR tracks this site publishes.
#:
#: Deliberately the public production URL: UCSC fetches the hub server-side, so
#: a localhost address could never be reached, and a development instance
#: pointing at production's hub shows exactly the same tracks.
_HUB_URL = "https://genetrek.pasteur.fr/data/ucsc/genetrek_pasteur_hub.txt"

#: The hub file declares ``genome hg38``, so it applies to that assembly alone.
_HUB_ASSEMBLY = "GRCh38"

#: A ``{column}`` reference in an external link template. Mirrors the pattern
#: ``db.database`` validates these with; not imported from there, because the
#: view layer should not reach into the maintenance library.
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

#: One column, shared by both assemblies -- a gene does not change strand
#: between them -- so it carries no coordinate role and is read by name. Listed
#: in ``schema.CODE_DEPENDENT_COLUMNS``, and optional there: a database without
#: it just shows no strand.
_STRAND_COLUMN = "strand"

#: HGNC's curated cytogenetic band. Like the strand it carries no role -- its
#: own description says it is "not derived from the coordinates in this
#: database" -- so it is read by name and listed in
#: ``schema.CODE_DEPENDENT_COLUMNS`` as optional.
_LOCATION_COLUMN = "hgnc_location"

#: How long a copy button shows its checkmark before reverting.
_COPIED_FEEDBACK_SECONDS = 1.5

#: Rows built for the identifier block. Five are filled today (NCBI,
#: Ensembl, HGNC, OMIM, cytogenetic band); the slots are fixed because the
#: dialog is built once, and a database filling fewer just hides the rest.
_MAX_IDENTIFIERS = 5


def _value(row: dict[str, Any], column: str | None) -> str:
    """A row's value for a column as a trimmed string, empty when absent."""
    if column is None:
        return ""
    value = row.get(column)
    return "" if value is None else str(value).strip()


def format_locus(row: dict[str, Any], locus: LocusColumns) -> str | None:
    """Render one assembly's coordinates, or ``None`` if the gene lacks them.

    Coverage is per assembly, not global: 278 genes have GRCh38 coordinates and
    no GRCh37 ones, and 42 the reverse, so each assembly is asked separately.
    """
    chrom = _value(row, locus.chrom)
    start, end = row.get(locus.start), row.get(locus.end)
    if not chrom or start is None or end is None:
        return None
    span = f"{int(start):,}-{int(end):,}"
    return f"{chrom}:{span} ({int(end) - int(start) + 1:,} bp)"


def plain_locus(row: dict[str, Any], locus: LocusColumns) -> str | None:
    """Render one assembly's coordinates for the clipboard.

    Separator-free and ``chr``-prefixed, which is the one spelling every
    consumer accepts: this application's own search, UCSC, Ensembl, IGV and
    samtools. Thousands separators would be actively harmful -- pasted back
    into the search box they are split on, and the fragments are then read as
    NCBI gene IDs (see ``_COMMA_LOCUS_RE`` in ``services/search.py``).

    Returns:
        ``chr22:50674408-50733212``, or ``None`` if the gene has no coordinates
        in this assembly.
    """
    chrom = _value(row, locus.chrom)
    start, end = row.get(locus.start), row.get(locus.end)
    if not chrom or start is None or end is None:
        return None
    # A database may already store the prefix; do not double it.
    if not chrom.lower().startswith("chr"):
        chrom = f"chr{chrom}"
    return f"{chrom}:{int(start)}-{int(end)}"


def identifier_rows(config: AppConfig, row: dict[str, Any]) -> list[tuple[str, str]]:
    """The gene's identifiers, as label/value pairs, empty ones omitted.

    Labels come from the field metadata rather than being written here, so a
    database renaming "NCBI ID" shows its own wording. The columns come from
    the roles, except the cytogenetic band which has none.
    """
    roles = config.roles
    columns = [
        roles.ncbi,
        roles.ensg,
        roles.hgnc,
        roles.omim,
        _LOCATION_COLUMN,
    ]
    rows: list[tuple[str, str]] = []
    for column in columns:
        if column is None:
            continue
        value = _value(row, column)
        if not value:
            continue
        meta = config.display_config.get(column) or {}
        rows.append((str(meta.get("fullname") or column), value))
    return rows


def resolve_gene(config: AppConfig, query: str) -> str | None:
    """Find a gene by primary key or by symbol, and return its primary key.

    Two callers, two kinds of value: a row click sends the primary key, a
    ``?gene=`` link carries a symbol somebody typed. Both are resolved here so
    neither has to know what the other sends. Symbols match case-insensitively,
    since a URL is written by hand.

    Returns:
        The primary-key value, or ``None`` if nothing matches.
    """
    key_column = config.roles.primary_key
    if key_column is None or key_column not in config.df.columns:
        return None
    query = query.strip()
    if not query:
        return None

    keys = config.df[key_column].cast(str)
    if (exact := config.df.filter(keys == query)).height:
        return str(exact[key_column][0])

    symbol = config.roles.symbol
    if symbol is not None and symbol in config.df.columns:
        wanted = query.upper()
        matched = config.df.filter(
            config.df[symbol].cast(str).str.to_uppercase() == wanted,
        )
        if matched.height:
            return str(matched[key_column][0])
    return None


def ucsc_url(assembly: str, row: dict[str, Any], roles: Roles) -> str | None:
    """A UCSC genome-browser link for one assembly, positioned on the gene."""
    database = _UCSC_DB.get(assembly)
    gene = _value(row, roles.symbol)
    if database is None or not gene:
        return None
    url = f"https://genome.ucsc.edu/cgi-bin/hgTracks?db={database}"
    if assembly == _HUB_ASSEMBLY:
        url += f"&hubUrl={quote(_HUB_URL, safe=':/')}"
    # Escaped rather than interpolated: no symbol in the current database needs
    # it, but one carrying an '&' or a '#' would silently truncate the query.
    return f"{url}&position={quote(gene, safe='')}"


def ensembl_url(assembly: str, row: dict[str, Any], roles: Roles) -> str | None:
    """An Ensembl link for one assembly, by gene ID rather than by symbol."""
    host = _ENSEMBL_HOST.get(assembly)
    gene_id = _value(row, roles.ensg)
    if host is None or not gene_id:
        return None
    return f"https://{host}/Homo_sapiens/Gene/Summary?g={quote(gene_id, safe='')}"


def resolve_external_links(
    links_config: dict[str, Any],
    row: dict[str, Any],
) -> list[tuple[str, str]]:
    """Fill in each configured link's ``{column}`` placeholders for one gene.

    A link whose placeholder the gene has no value for is **dropped**, not
    rendered half-substituted -- so a gene without an OMIM entry simply has no
    OMIM link. This reproduces what the row dropdown did before the dialog
    replaced it.

    Returns:
        ``(name, url)`` pairs, in configured order.
    """
    resolved: list[tuple[str, str]] = []
    for link in links_config.get("links") or []:
        if not isinstance(link, dict):
            continue
        url = str(link.get("url", ""))
        for column in _PLACEHOLDER_RE.findall(url):
            value = _value(row, column)
            if value:
                url = url.replace(f"{{{column}}}", value)
        if "{" in url or not url:
            continue
        resolved.append((str(link.get("name") or url), url))
    return resolved


def _assemblies_newest_first(roles: Roles) -> list[str]:
    """Assembly names, current genome first.

    Named assemblies sort ascending by version, so descending puts the current
    one at the top -- where a reader looks first -- without this module knowing
    what any of them are called. It is the same assumption
    ``search_drawer`` makes when it defaults to ``sorted(assemblies)[-1]``.
    """
    return sorted(roles.assemblies, reverse=True)


def _browser_links(row: dict[str, Any], roles: Roles) -> list[tuple[str, str]]:
    """UCSC and Ensembl links, one of each per assembly the mappings know."""
    links: list[tuple[str, str]] = []
    for assembly in _assemblies_newest_first(roles):
        if url := ucsc_url(assembly, row, roles):
            label = f"UCSC {assembly}"
            if assembly == _HUB_ASSEMBLY:
                label += " (with GeneTrek tracks)"
            links.append((label, url))
    links.extend(
        (f"Ensembl {assembly}", url)
        for assembly in _assemblies_newest_first(roles)
        if (url := ensembl_url(assembly, row, roles))
    )
    return links


def _as_markdown_links(links: list[tuple[str, str]]) -> str:
    return " · ".join(f"[{name}]({url})" for name, url in links)


def gene_links_markdown(config: AppConfig, row: dict[str, Any]) -> str:
    """Build the link half of the dialog: genome browsers, then external ones.

    The coordinates and identifiers are rendered as elements rather than
    markdown, because each of their rows carries a copy button and markdown
    cannot hold one. Kept a pure function so it can be tested without a browser.

    Args:
        config: Supplies ``roles`` and the external-link templates.
        row: One row of the frame, as a dict.

    Returns:
        Markdown, for rendering with :data:`MARKDOWN_EXTRAS`.
    """
    blocks: list[str] = []

    browsers = _browser_links(row, config.roles)
    if browsers:
        blocks.append("**Genome browsers**\n\n" + _as_markdown_links(browsers))

    # The generated external_links.yaml carries an Ensembl entry of its own, so
    # without this the dialog would offer the same URL twice. Compared on the
    # final URL rather than on the name, which stays exact if the config changes.
    already = {url for _, url in browsers}
    external = [
        (name, url)
        for name, url in resolve_external_links(config.external_links_config, row)
        if url not in already
    ]
    if external:
        blocks.append("**External resources**\n\n" + _as_markdown_links(external))

    return "\n\n".join(blocks)


def _copy_button(payload: Callable[[], str]) -> ui.button:
    """A small copy-to-clipboard button, reading its text when clicked.

    The payload is a callable rather than a value because one button serves
    every gene: the dialog is built once and refilled, so what to copy is only
    known at click time.
    """
    # `dense` matters for more than the button: it sets the row's height, so
    # without it every fact line is padded out to Quasar's 30px minimum.
    button = ui.button(icon="content_copy").props("round flat dense size=sm")
    button.classes("opacity-40 hover:opacity-100").style("color: #38ada9;")

    async def copy() -> None:
        # ui.clipboard.write json.dumps-escapes the payload and guards the
        # secure-context requirement, which the deployment satisfies (HTTPS in
        # production, localhost in development).
        ui.clipboard.write(payload())
        # Feedback in place, rather than a toast: this application has never
        # used ui.notify, and reports state by mutating the element you are
        # looking at.
        button.props("icon=check")
        await asyncio.sleep(_COPIED_FEEDBACK_SECONDS)
        button.props("icon=content_copy")

    button.on_click(copy)
    return button


@dataclass(frozen=True)
class _FactRow:
    """One ``label  value  [copy]`` line of the dialog, held by its parts.

    Kept as named references rather than reached for through the element tree:
    the alternative is indexing into ``default_slot.children``, which silently
    breaks the day a row gains an element.
    """

    row: ui.row
    label: ui.label
    value: ui.label
    copy: ui.button

    def fill(self, label: str, value: str, *, copyable: bool = True) -> None:
        """Show this row for one gene."""
        self.label.set_text(label)
        self.value.set_text(value)
        self.copy.set_visibility(copyable)
        self.row.set_visibility(True)

    def hide(self) -> None:
        self.row.set_visibility(False)


def _section(heading: str) -> ui.column:
    """A titled block whose lines sit tight against each other.

    The dialog card is a flex column with a 16px gap, which is right *between*
    sections and far too loose between the lines of one. So each section owns a
    gapless column and the card's gap only ever separates whole sections.
    """
    column = ui.column().classes("w-full gap-0")
    with column:
        ui.label(heading).classes("text-sm font-bold mb-1")
    return column


def _fact_row(label: str, payload: Callable[[], str]) -> _FactRow:
    """Build a ``label  value  [copy]`` line."""
    with ui.row().classes("w-full items-center gap-2 no-wrap") as row:
        label_el = (
            ui.label(label)
            .classes("text-sm")
            .style("color: #718096; min-width: 140px;")
        )
        # Grows to fill the row, which pushes every copy button out to the
        # same right-hand edge instead of trailing its own value.
        value = ui.label().classes("text-sm font-medium grow")
        copy = _copy_button(payload)
    return _FactRow(row=row, label=label_el, value=value, copy=copy)


def create_gene_info_dialog(
    config: AppConfig,
) -> tuple[ui.dialog, Callable[[str], None]]:
    """Build the gene-info dialog once, and return it with its opener.

    One dialog is reused for every gene, for the same reason the column dialog
    is: a ``ui.dialog`` is only hidden on close, so one per click would leave an
    element tree per click behind for the lifetime of the page.

    The rows are built once too. That is possible because both sets are fixed
    for the life of the process -- the assemblies come from the metadata and the
    identifier columns from the roles -- so a click only sets text and hides the
    rows this gene has nothing for.

    Returns:
        The dialog, and a ``show(query)`` taking a primary key or a symbol.
    """
    # What the buttons copy, replaced wholesale on each open. A dict rather
    # than closures over a value, because the buttons outlive any one gene.
    clipboard: dict[str, str] = {}
    current: dict[str, str] = {}

    def payload_for(name: str) -> Callable[[], str]:
        return lambda: clipboard.get(name, "")

    dialog = ui.dialog()
    with dialog, ui.card().style("width: 640px; max-width: 90vw;"):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            title = ui.label().classes("text-lg font-bold").style("color: #1a365d;")
            link_button = ui.button(icon="link").props("round flat size=sm")
            link_button.classes("opacity-40 hover:opacity-100")
            link_button.style("color: #38ada9;")
            link_button.tooltip("Copy a link to this gene")

        assemblies = _assemblies_newest_first(config.roles)
        coordinates: dict[str, _FactRow] = {}
        strand: _FactRow | None = None
        if assemblies:
            with _section("Coordinates"):
                coordinates = {
                    assembly: _fact_row(assembly, payload_for(assembly))
                    for assembly in assemblies
                }
                strand = _fact_row("Strand", payload_for(_STRAND_COLUMN))

        identifiers_section = _section("Identifiers")
        with identifiers_section:
            identifier_slots = [
                _fact_row("", payload_for(f"id{index}"))
                for index in range(_MAX_IDENTIFIERS)
            ]

        links = ui.markdown(extras=MARKDOWN_EXTRAS).classes("text-sm")
        with ui.row().classes("w-full justify-end"):
            ui.button("Close", on_click=dialog.close).props("flat")

    async def copy_link() -> None:
        # Not ui.clipboard.write: the origin has to come from the browser.
        # Behind a reverse proxy a server-built base URL can report the wrong
        # scheme, and only the page knows where it was actually loaded from.
        # Same secure-context guard nicegui's own clipboard helper uses.
        gene = json.dumps(current.get("gene", ""))
        ui.run_javascript(f"""
            if (navigator.clipboard) {{
                navigator.clipboard.writeText(
                    location.origin + '/?gene=' + encodeURIComponent({gene})
                )
            }} else {{
                console.error('Clipboard API needs a secure context (HTTPS or localhost).')
            }}
        """)
        link_button.props("icon=check")
        await asyncio.sleep(_COPIED_FEEDBACK_SECONDS)
        link_button.props("icon=link")

    link_button.on_click(copy_link)

    def show(query: str) -> None:
        # The value arrives from the browser or a URL, so it is resolved rather
        # than trusted: anything unknown means a stale client or a bad link,
        # and opens nothing.
        key = resolve_gene(config, query)
        # A non-unique primary key is a data fault, not something a dialog can
        # report: opening nothing is the same outcome as an unknown gene.
        try:
            row = None if key is None else lookup.gene_row(config.df, config.roles, key)
        except lookup.AmbiguousGeneError:
            return
        if key is None or row is None:
            return

        symbol = _value(row, config.roles.symbol)
        title.set_text(symbol or key)
        current["gene"] = symbol or key
        clipboard.clear()

        for assembly, fact in coordinates.items():
            locus = config.roles.assemblies[assembly]
            plain = plain_locus(row, locus)
            # An absent assembly is stated rather than skipped -- "we have no
            # GRCh37 for this gene" is worth knowing -- but with no coordinates
            # there is nothing to copy, so the button would be a dead control.
            fact.fill(
                assembly,
                format_locus(row, locus) or "not available for this gene",
                copyable=plain is not None,
            )
            if plain is not None:
                clipboard[assembly] = plain

        strand_value = _value(row, _STRAND_COLUMN)
        if strand is not None:
            if strand_value:
                strand.fill("Strand", strand_value)
                clipboard[_STRAND_COLUMN] = strand_value
            else:
                strand.hide()

        identifiers = identifier_rows(config, row)
        for index, slot in enumerate(identifier_slots):
            if index >= len(identifiers):
                slot.hide()
                continue
            label, value = identifiers[index]
            slot.fill(label, value)
            clipboard[f"id{index}"] = value
        identifiers_section.set_visibility(bool(identifiers))

        links.set_content(gene_links_markdown(config, row))
        dialog.open()

    return dialog, show
