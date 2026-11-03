"""A set of genes as clipboard text: one identifier per line.

Shared by every "Copy genes" button -- the current results, a database gene
list, one of My lists -- so they agree on the format: the identifiers of one
role, in the frame's order, each once, with no header. One per line pastes as a
column into a spreadsheet and as separate tokens into the search box, here or
in another tool.

Like :mod:`genetrek_server.services.search`, this takes a frame and a
:class:`~genetrek_server.schema.Roles`, never an ``AppConfig``, and names no column.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from genetrek_server.schema import IDENTIFIER_LABELS, IDENTIFIER_ROLES

if TYPE_CHECKING:
    from genetrek_server.schema import Roles


#: The identifier roles a list can be copied as, in menu order; the first one a
#: database fills (the symbol) is the default. OMIM is left out: a gene carries
#: several MIM numbers in one cell, so it is no gene's single identifier.
COPY_ROLES: tuple[str, ...] = tuple(r for r in IDENTIFIER_ROLES if r != "omim")


@dataclass(frozen=True)
class GeneText:
    """What one copy puts on the clipboard, and what it left out."""

    text: str
    #: Identifiers in ``text``.
    copied: int
    #: Genes with no identifier of the chosen role (null or empty).
    without_id: int


def copy_roles(roles: Roles) -> list[tuple[str, str]]:
    """The ``(role, label)`` pairs this database can copy, default first."""
    return [
        (role, IDENTIFIER_LABELS[role])
        for role in COPY_ROLES
        if getattr(roles, role, None) is not None
    ]


def gene_text(frame: pl.DataFrame, roles: Roles, role: str) -> GeneText:
    """The genes in ``frame`` as one ``role`` identifier per line.

    Order is the frame's, so the current results copy in the order the table
    shows them. A gene with no identifier of that role is counted, not
    guessed: a list copied as Ensembl ids must not quietly shrink.

    Raises:
        ValueError: If ``role`` is not a copyable role this database fills.
    """
    column = getattr(roles, role, None) if role in COPY_ROLES else None
    if column is None or column not in frame.columns:
        msg = f"cannot copy genes as {role!r}: no such identifier in this database"
        raise ValueError(msg)
    values = frame.get_column(column).cast(pl.String).str.strip_chars()
    present = values.filter(values.is_not_null() & (values != ""))
    unique = present.unique(maintain_order=True).to_list()
    return GeneText(
        text="\n".join(unique),
        copied=len(unique),
        without_id=frame.height - present.len(),
    )
