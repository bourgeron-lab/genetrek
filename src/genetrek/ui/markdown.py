"""How this application renders markdown.

Three places render prose or metadata written by hand: the site description on
the main page, the documentation page, and the column dialog. They share one
extras list because they draw on the same vocabulary -- column names, flag
values, DOIs -- and because a link that behaves differently on one page than
another is a bug nobody thinks to look for.
"""

from __future__ import annotations

#: markdown2 extras for every ``ui.markdown`` in the application.
#:
#: Beyond NiceGUI's default ``fenced-code-blocks`` and ``tables``:
#:
#: - ``code-friendly`` stops ``_`` being read as emphasis. Our prose is full of
#:   identifiers (``low_exome_coverage``, ``no_exp_lof``, ``shet_mean``), and
#:   the default turns those into ``low<em>exome</em>coverage``. ``*text*``
#:   still emphasises, which some field descriptions rely on.
#: - ``target-blank-links`` sends outbound links to a new tab. Everything here
#:   is one NiceGUI page, so a same-tab navigation to a DOI would tear down the
#:   client and discard the table's filters, sort and pagination.
MARKDOWN_EXTRAS: list[str] = [
    "fenced-code-blocks",
    "tables",
    "code-friendly",
    "target-blank-links",
    # Heading ids, so the prose pages can be deep-linked and can carry their
    # own contents list. Note `target-blank-links` matches every href that is
    # not a fragment, so a `#section` link stays in this tab while an outbound
    # one still opens a new one.
    "header-ids",
]
