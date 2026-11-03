"""Comparing coexisting versions of a gene list.

The changelog's prose is written, but its arithmetic is computed here. Two of
the five numeric claims already hand-written into ``fields_description.yaml``
have gone stale, which is the argument for not typing these numbers at all.

Families are seeded from the **aliases**, never from a version regex on column
names. An alias is the explicit "this is the current one" pointer -- see
``docs/database-maintenance.md`` on why version strings are not sorted -- and a
regex is wrong in both directions: it invents families out of names like
``inheritance_panelapp_20260617`` (Categorical, so a set operation raises) and
``is_loeuf_gnomad_v4_1_1`` (Boolean but 27,084 nulls, so set arithmetic is
meaningless), while missing names whose version is not the last segment, such
as ``cancer_ncg_7_2`` and ``trrust_v2_tf``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class Comparison:
    """One older version measured against its family's current one."""

    older: str
    current: str
    n_older: int
    n_current: int
    in_both: int
    #: In the current version and not the older one.
    added: int
    #: In the older version and not the current one.
    dropped: int
    #: Intersection over union. Reported rather than a correlation: phi reads
    #: systematically higher than the overlap it would be mistaken for, over a
    #: table where the overwhelming majority of rows are false in both.
    jaccard: float


@dataclass(frozen=True)
class VersionFamily:
    """Versions of one gene list that still coexist in the table."""

    #: The stable short name, which is the alias.
    alias: str
    #: The column the alias points at.
    current: str
    comparisons: list[Comparison] = field(default_factory=list)
    #: Columns in the family that could not be compared, and why.
    skipped: list[tuple[str, str]] = field(default_factory=list)


def _unusable(df: pl.DataFrame, column: str) -> str | None:
    """Why ``column`` cannot take part in set arithmetic, or ``None``."""
    dtype = df.schema.get(column)
    if dtype is None:
        return "not in the table"
    if dtype != pl.Boolean:
        return f"not a gene list ({dtype})"
    if df[column].null_count():
        # A null is neither in nor out of the set, so "added" and "dropped"
        # would both be guesses.
        return f"{df[column].null_count():,} genes have no value"
    return None


def compare_boolean_columns(df: pl.DataFrame, older: str, current: str) -> Comparison:
    """Measure two gene lists against each other.

    Both columns must be boolean and null-free; :func:`version_families`
    filters on that before calling this.
    """
    a, b = df[older], df[current]
    in_both = int((a & b).sum())
    n_older, n_current = int(a.sum()), int(b.sum())
    union = n_older + n_current - in_both
    return Comparison(
        older=older,
        current=current,
        n_older=n_older,
        n_current=n_current,
        in_both=in_both,
        added=n_current - in_both,
        dropped=n_older - in_both,
        jaccard=in_both / union if union else 0.0,
    )


def version_families(
    df: pl.DataFrame,
    aliases: Mapping[str, str],
) -> list[VersionFamily]:
    """Every aliased gene list that has older versions still in the table.

    A family with no surviving older version is omitted: there is nothing to
    compare, and listing it would only say that a column exists.
    """
    families: list[VersionFamily] = []
    for alias, current in sorted(aliases.items()):
        if _unusable(df, current) is not None:
            continue
        prefix = f"{alias}_"
        siblings = sorted(
            column
            for column in df.columns
            if column != current and column.startswith(prefix)
        )
        if not siblings:
            continue

        comparisons: list[Comparison] = []
        skipped: list[tuple[str, str]] = []
        for sibling in siblings:
            if (reason := _unusable(df, sibling)) is not None:
                skipped.append((sibling, reason))
                continue
            comparisons.append(compare_boolean_columns(df, sibling, current))
        if comparisons or skipped:
            families.append(
                VersionFamily(
                    alias=alias,
                    current=current,
                    comparisons=comparisons,
                    skipped=skipped,
                ),
            )
    return families


def version_table_markdown(
    df: pl.DataFrame,
    aliases: Mapping[str, str],
) -> str:
    """Render the family comparisons as a markdown table.

    Markdown rather than a Quasar table so it is styled by the same rules as
    the authored tables around it, and so this stays a pure function.

    Returns an empty string when no family has a surviving older version, so
    the caller can omit the section entirely rather than show an empty table.
    """
    families = version_families(df, aliases)
    if not families:
        return ""

    # The family is named by its alias rather than by the current column's
    # full name, which would repeat the version in every row of the family and
    # say "v7" beside `hc_ndd_v3`. The alias is also the name a reader would
    # actually type.
    lines = [
        "| Gene list | Version | Genes | Shared with current "
        "| Added in current | Dropped in current | Jaccard |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for family in families:
        label = f"`{family.alias}`"
        for comparison in family.comparisons:
            lines.append(  # noqa: PERF401 - the loop also emits the skip rows
                f"| {label} | `{comparison.older}` | {comparison.n_older:,} "
                f"| {comparison.in_both:,} | {comparison.added:,} "
                f"| {comparison.dropped:,} | {comparison.jaccard:.3f} |",
            )
        for column, reason in family.skipped:
            lines.append(
                f"| {label} | `{column}` | — | — | — | — | not comparable: {reason} |",
            )
        current_genes = (
            family.comparisons[0].n_current
            if family.comparisons
            else int(df[family.current].sum())
        )
        lines.append(
            f"| {label} | **`{family.current}`** (current) "
            f"| {current_genes:,} | — | — | — | — |",
        )
    return "\n".join(lines)
