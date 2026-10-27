"""The per-column licence file, ``DATA_LICENCES.md``, built from the metadata.

No single licence covers the database: its columns come from sources whose
terms differ, from CC0 to non-commercial. So each column declares its own in
``FieldMeta.licence``, and this module turns those into the file that ships
with the code and with every data deposit. Generated, never hand-edited, so it
cannot drift from the columns; ``tests/test_real_database.py`` holds the
committed copy to the metadata.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from genetrek import schema

if TYPE_CHECKING:
    from collections.abc import Mapping

    from genetrek.schema import CategoryOrder, FieldMeta

#: What a column without a declared licence shows, so the gap stays visible.
UNDECLARED = "not yet declared"

_PREAMBLE = """\
# Data licences

GeneTrek's code is under the MIT licence (see `LICENSE`). Its **data** is not:
every column comes from a source with its own terms, listed below, and no
single licence covers the whole database. When you reuse a column, follow its
source's terms and cite its source; the [How to cite](https://genetrek.pasteur.fr/cite)
page and each column's information dialog give the references.

GeneTrek is for research use only, not for diagnostic use.

This file is generated from each column's metadata by `genetrek-db licences`.
Do not edit it by hand.
"""


def _cell(text: str) -> str:
    """Make free text safe inside a markdown table cell."""
    return " ".join(text.split()).replace("|", "\\|")


def licences_markdown(
    fields: Mapping[str, FieldMeta],
    categories: CategoryOrder,
) -> str:
    """Render ``DATA_LICENCES.md`` for these fields, in display order.

    Hidden columns are included: the API serves them and the download carries
    them, so their terms apply as much as any visible column's.
    """
    order = schema.order_fields(fields, categories)
    counts = Counter(fields[name].licence or UNDECLARED for name in order)

    lines = [_PREAMBLE, "## Summary", "", "| Licence | Columns |", "| --- | ---: |"]
    lines += [
        f"| {_cell(licence)} | {n} |"
        for licence, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    lines += [
        "",
        "## Columns",
        "",
        "| Column | Name | Licence | Source | DOI |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name in order:
        meta = fields[name]
        lines.append(
            f"| `{name}` | {_cell(meta.fullname)} | "
            f"{_cell(meta.licence or UNDECLARED)} | {_cell(meta.source)} | "
            f"{_cell(meta.doi)} |",
        )
    return "\n".join(lines) + "\n"
