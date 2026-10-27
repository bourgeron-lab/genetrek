from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from genetrek.schema import Roles

    #: Maps symbols onto primary keys through the gene index's alias and
    #: previous-symbol layers (``services.lookup.symbol_resolver``).
    SymbolResolver = Callable[[Sequence[str]], Sequence[str]]


#: A locus token: an optional ``chr``, a chromosome, and a span or a single
#: position (``22:50700000``, read as a one-base span -- a variant's locus).
#:
#: Mitochondria are spelled ``M`` by UCSC and ``MT`` by Ensembl and the
#: 1000G-style references. Both are accepted here and reconciled against
#: whichever one the database stores by :func:`_chromosome_aliases`.
_LOCUS_RE = re.compile(
    r"^(?:chr)?(\d+|X|Y|MT|M):(\d+)(?:-(\d+))?$",
    re.IGNORECASE,
)

#: A locus that still carries thousands separators, so they can be stripped
#: before the token split gets to them.
#:
#: Genome browsers print coordinates with separators -- UCSC, which this
#: application links out to, shows ``chr22:50,674,408-50,733,212`` -- so pasting
#: one back in is an obvious thing to do. Left alone, the split on commas below
#: turns that into ``22:50`` (dropped) plus ``674``, ``733`` and ``212``, which
#: are then read as *NCBI gene IDs* and return two unrelated genes on other
#: chromosomes. A wrong answer offered confidently, so it is worth pre-empting.
#:
#: The span deliberately cannot end on a comma: ``\d(?:[\d,]*\d)?`` starts and
#: ends on a digit, so ``22:100-200,TP53`` keeps its boundary instead of the
#: strip swallowing the following token.
_COMMA_LOCUS_RE = re.compile(
    r"(?:chr)?(?:\d+|X|Y|MT|M):\d(?:[\d,]*\d)?-\d(?:[\d,]*\d)?",
    re.IGNORECASE,
)

#: Spellings of the mitochondrial contig that all mean the same thing.
_MITO_SPELLINGS = ("M", "MT")


def _chromosome_aliases(chromosome: str) -> tuple[str, ...]:
    """Every spelling of a queried chromosome that should match the data.

    Only mitochondria have more than one; everything else is itself.
    """
    if chromosome in _MITO_SPELLINGS:
        return _MITO_SPELLINGS
    return (chromosome,)


class SearchUnavailableError(RuntimeError):
    """No column the search can use is present in the database.

    Raised rather than returning every row: a full unfiltered table looks
    identical to "no search was run", which is the most misleading thing this
    module could do.
    """


def filter_dataframe(  # noqa: PLR0913
    df: pl.DataFrame,
    search_text: str,
    *,
    exact_match: bool,
    selected_bool_cols: list[str],
    results_merge: str,
    annotations_merge: str,
    selected_genome: str,
    roles: Roles,
    symbol_resolver: SymbolResolver | None = None,
) -> pl.DataFrame:
    """Apply token-based search and boolean column filtering to the DataFrame.

    Args:
        df: Full unfiltered DataFrame.
        search_text: Comma/space/semicolon-separated tokens.
        exact_match: Use exact matching for gene symbols.
        selected_bool_cols: Boolean annotation columns to filter on.
        results_merge: How to merge token and annotation results ("union" or "intersection").
        annotations_merge: How to merge multiple annotations ("union" or "intersection").
        selected_genome: Assembly name for loci lookup, a key of
            ``roles.assemblies``.
        roles: Which column fills each semantic role, so the search does not
            depend on any column being called a particular thing.
        symbol_resolver: Optional; with exact matching, symbols are also
            resolved through it, so a withdrawn or alias symbol
            (``KIAA1109``) finds the gene it names now (``BLTP1``).

    Returns:
        Filtered DataFrame.

    Raises:
        SearchUnavailableError: If a search was requested but no usable column
            exists.
    """
    if search_text and search_text.strip() and not roles.searchable:
        msg = (
            "this database has no symbol, Ensembl, NCBI or coordinate column, so "
            "gene search cannot be performed"
        )
        raise SearchUnavailableError(msg)

    token_condition = _build_token_condition(
        df,
        search_text,
        exact_match=exact_match,
        selected_genome=selected_genome,
        roles=roles,
        symbol_resolver=symbol_resolver,
    )
    bool_condition = _build_bool_condition(
        df,
        selected_bool_cols,
        annotations_merge=annotations_merge,
    )

    final_condition = _merge_conditions(
        token_condition,
        bool_condition,
        merge_mode=results_merge,
    )

    if final_condition is not None:
        return df.filter(final_condition)
    return df


def _build_token_condition(  # noqa: PLR0912, PLR0913
    df: pl.DataFrame,
    search_text: str,
    *,
    exact_match: bool,
    selected_genome: str,
    roles: Roles,
    symbol_resolver: SymbolResolver | None = None,
) -> pl.Expr | None:
    if not search_text or not search_text.strip():
        return None

    # Separators inside a locus go before the split, never after: once the text
    # is in pieces the digits are indistinguishable from gene IDs.
    cleaned = _COMMA_LOCUS_RE.sub(
        lambda m: m.group(0).replace(",", ""),
        search_text.strip(),
    )
    tokens = re.split(r"[,;\s\t\n]+", cleaned)
    tokens = [t.strip() for t in tokens if t.strip()]

    ensembl_ids: list[str] = []
    ncbi_ids: list[str] = []
    hgnc_ids: list[str] = []
    gene_symbols: list[str] = []
    loci: list[tuple[str, int, int]] = []

    for token in tokens:
        # HGNC's canonical form carries a colon, so it has to be recognised
        # before the locus branch claims every colon-bearing token.
        if (bare := token.upper().removeprefix("HGNC:")) != token.upper():
            if bare.isdigit():
                hgnc_ids.append(bare)
        elif ":" in token:
            if locus_match := _LOCUS_RE.match(token):
                chr_val = locus_match.group(1).upper()
                start_val = int(locus_match.group(2))
                end_val = int(locus_match.group(3) or locus_match.group(2))
                loci.append((chr_val, start_val, end_val))
        elif re.match(r"^ENSG\d{11}", token):
            ensembl_ids.append(token.split(".")[0])
        elif re.match(r"^\d+$", token):
            ncbi_ids.append(token)
        elif re.match(r"^[A-Za-z0-9-]+$", token):
            gene_symbols.append(token.upper())

    conditions: list[pl.Expr] = []

    if ensembl_ids and roles.ensg in df.columns:
        conditions.append(pl.col(roles.ensg).is_in(ensembl_ids))

    if ncbi_ids and roles.ncbi in df.columns:
        conditions.append(pl.col(roles.ncbi).cast(pl.Utf8).is_in(ncbi_ids))

    if hgnc_ids and roles.hgnc in df.columns:
        # Stored bare (14294) by an older database, canonically prefixed
        # (HGNC:14294) by one built with 'genetrek-db init'. Match either.
        conditions.append(
            pl.col(roles.hgnc).cast(pl.Utf8).str.replace("^HGNC:", "").is_in(hgnc_ids),
        )

    if gene_symbols and roles.symbol is not None and roles.symbol in df.columns:
        conditions.extend(
            _symbol_conditions(
                gene_symbols,
                symbol_col=roles.symbol,
                exact_match=exact_match,
                key=roles.primary_key if roles.primary_key in df.columns else None,
                symbol_resolver=symbol_resolver,
            ),
        )

    if loci:
        loci_cond = _build_loci_condition(df, loci, selected_genome, roles)
        if loci_cond is not None:
            conditions.append(loci_cond)

    if not conditions:
        # Tokens were given but none could be turned into a filter: an
        # unrecognised identifier, or one whose column this database lacks.
        # Returning None here would hand back the *entire* table and report it
        # as a result count, which reads exactly like a successful search.
        return pl.lit(value=False)

    result = conditions[0]
    for cond in conditions[1:]:
        result = result | cond
    return result


def _symbol_conditions(
    gene_symbols: list[str],
    *,
    symbol_col: str,
    exact_match: bool,
    key: str | None,
    symbol_resolver: SymbolResolver | None,
) -> list[pl.Expr]:
    """Symbol tokens: exact (plus resolved aliases) or substring matches."""
    symbol = pl.col(symbol_col).str.to_uppercase()
    if not exact_match:
        combined = symbol.str.contains(gene_symbols[0])
        for sym in gene_symbols[1:]:
            combined = combined | symbol.str.contains(sym)
        return [combined]
    conditions = [symbol.is_in(gene_symbols)]
    if symbol_resolver is None or key is None:
        return conditions
    # Withdrawn and alias symbols, resolved by the gene index.
    if keys := list(symbol_resolver(gene_symbols)):
        conditions.append(pl.col(key).cast(pl.Utf8).is_in(keys))
    return conditions


def _build_loci_condition(
    df: pl.DataFrame,
    loci: list[tuple[str, int, int]],
    selected_genome: str,
    roles: Roles,
) -> pl.Expr | None:
    """Build the interval condition for one assembly's coordinate columns."""
    locus = roles.assemblies.get(selected_genome)
    if locus is None:
        return None
    chr_col, start_col, end_col = locus.chrom, locus.start, locus.end

    if not all(col in df.columns for col in (chr_col, start_col, end_col)):
        return None

    # Compared on the bare form, so a database storing 'chr22' and a query
    # saying '22' still meet. The prefix strip is anchored: an unanchored
    # replace would also eat a 'chr' occurring inside a contig name.
    bare_chromosome = pl.col(chr_col).cast(pl.Utf8).str.replace("(?i)^chr", "")

    loci_conditions: list[pl.Expr] = []
    for locus_chr, locus_start, locus_end in loci:
        chr_match = bare_chromosome.is_in(list(_chromosome_aliases(locus_chr)))
        overlap = (pl.col(start_col) <= locus_end) & (pl.col(end_col) >= locus_start)
        loci_conditions.append(chr_match & overlap)

    if not loci_conditions:
        return None

    result = loci_conditions[0]
    for cond in loci_conditions[1:]:
        result = result | cond
    return result


def _build_bool_condition(
    df: pl.DataFrame,
    selected_bool_cols: list[str],
    *,
    annotations_merge: str,
) -> pl.Expr | None:
    """Keep the genes where the selected lists are True ("is on the list").

    ``pl.col(col)`` as a filter drops null as well as False, which is right for
    "is on it". A future "is *not* on it" filter must test ``== False`` rather
    than negate, so a gene the list never measured (null) is excluded from both.
    """
    if not selected_bool_cols:
        return None

    # A requested column the frame lacks counts as "no gene", never as "no
    # filter": skipping it would widen an intersection, and skipping every one
    # would return the whole table. A user list whose column was not built --
    # a stale selection after it was deleted in another tab -- is exactly that.
    conditions = [
        pl.col(col) if col in df.columns else pl.lit(value=False)
        for col in selected_bool_cols
    ]

    if annotations_merge == "intersection":
        result = conditions[0]
        for cond in conditions[1:]:
            result = result & cond
    else:
        result = conditions[0]
        for cond in conditions[1:]:
            result = result | cond
    return result


def _merge_conditions(
    token_condition: pl.Expr | None,
    bool_condition: pl.Expr | None,
    *,
    merge_mode: str,
) -> pl.Expr | None:
    if token_condition is not None and bool_condition is not None:
        if merge_mode == "intersection":
            return token_condition & bool_condition
        return token_condition | bool_condition
    if token_condition is not None:
        return token_condition
    return bool_condition


#: Most tokens a link may carry. A link is typed or generated, never a pasted
#: list; this bounds the per-token work :func:`search_link` does for its note.
MAX_LINK_TOKENS = 500

#: Unmatched tokens a note names before it summarises the rest.
_NOTE_LISTED = 10


@dataclass(frozen=True)
class SearchLink:
    """A search arriving in a URL (``?search=`` or ``#search=``)."""

    #: The tokens, comma-joined: the drawer's own value format.
    text: str
    genome: str | None
    tokens: tuple[str, ...] = field(default=())
    #: Tokens beyond :data:`MAX_LINK_TOKENS`, dropped.
    dropped: int = 0


def parse_search_link(
    search: str | None,
    genome: str | None,
    assemblies: Sequence[str],
) -> SearchLink | None:
    """Read a link's search text and assembly. ``None`` when there is no search.

    An unknown assembly falls back to the drawer's default (``None`` here),
    rather than silently matching no locus.
    """
    if not search or not search.strip():
        return None
    cleaned = _COMMA_LOCUS_RE.sub(
        lambda m: m.group(0).replace(",", ""),
        search.strip(),
    )
    tokens = [t for t in re.split(r"[,;\s]+", cleaned) if t]
    if not tokens:
        return None
    kept = tuple(dict.fromkeys(tokens))[:MAX_LINK_TOKENS]
    by_lower = {a.lower(): a for a in assemblies}
    chosen = by_lower.get((genome or "").strip().lower())
    return SearchLink(
        text=",".join(kept),
        genome=chosen,
        tokens=kept,
        dropped=max(0, len(dict.fromkeys(tokens)) - len(kept)),
    )


def unmatched_tokens(
    df: pl.DataFrame,
    tokens: Sequence[str],
    *,
    selected_genome: str,
    roles: Roles,
    symbol_resolver: SymbolResolver | None = None,
) -> list[str]:
    """The tokens that find no gene on their own, for a link's results note."""
    missing = []
    for token in tokens:
        condition = _build_token_condition(
            df,
            token,
            exact_match=True,
            selected_genome=selected_genome,
            roles=roles,
            symbol_resolver=symbol_resolver,
        )
        if condition is None or df.select(condition.any()).item() is not True:
            missing.append(token)
    return missing


def link_note(link: SearchLink, n_genes: int, missing: Sequence[str]) -> str:
    """The results note for a search that came from a link."""
    entries = len(link.tokens)
    note = (
        f"from link: {entries} entr{'y' if entries == 1 else 'ies'} → {n_genes} gene(s)"
    )
    if missing:
        shown = ", ".join(missing[:_NOTE_LISTED])
        extra = len(missing) - _NOTE_LISTED
        more = f" (+{extra} more)" if extra > 0 else ""
        note += f"; not found: {shown}{more}"
    if link.dropped:
        note += f"; {link.dropped} entries beyond {MAX_LINK_TOKENS} ignored"
    return note
