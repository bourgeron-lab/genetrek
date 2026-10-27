"""Resolving arbitrary gene identifiers onto the database's primary key.

Gene lists arrive keyed on whatever the source used: current symbols, symbols
that have since been renamed, Ensembl IDs with version suffixes, HGNC IDs with
their ``HGNC:`` prefix, or Entrez IDs that a spreadsheet turned into ``"1.0"``.
:class:`GeneIndex` normalises all of those and reports, per input, whether it
matched and how -- so a list can be checked before it is committed to the
database rather than silently losing rows to a failed join.

Symbols are the awkward case and get the most attention: they are not unique in
the database, and papers routinely cite names HGNC has since retired. Loading the
HGNC complete set adds alias and previous-symbol resolution.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from genetrek import paths
from genetrek.schema import Roles

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from genetrek.schema import ID_TYPE

#: Column names assumed when a caller supplies no :class:`~genetrek.schema.Roles`.
#: Present so existing callers keep working; new code should pass roles.
LEGACY_PRIMARY_KEY = "ncbi_id"
LEGACY_ID_COLUMNS: dict[str, str] = {
    "ncbi": "ncbi_id",
    "ensg": "ensg",
    "symbol": "symbol",
    "hgnc": "hgnc_id",
    "omim": "omim_id",
}

#: Identifier types stored as integers in the parquet.
_INTEGER_IDS = frozenset({"ncbi", "hgnc", "omim"})

#: Columns read from the HGNC complete set. Reading all 148 is pointlessly slow.
_HGNC_COLUMNS = ("hgnc_id", "symbol", "alias_symbol", "prev_symbol", "entrez_id")

#: Separator HGNC uses inside its multi-value symbol fields.
_HGNC_MULTI_SEP = "|"

_ENSG_RE = re.compile(r"^ENSG\d{11}(\.\d+)?$", re.IGNORECASE)
_HGNC_RE = re.compile(r"^HGNC:\d+$", re.IGNORECASE)
_INTEGER_RE = re.compile(r"^\d+$")
_VERSION_SUFFIX_RE = r"\.\d+$"

#: Non-key columns every resolution report carries, in order. The key column is
#: named after the database's own primary key, so reports stay self-describing.
_REPORT_TAIL: tuple[str, ...] = ("matched_by", "note")


class GeneIndexError(RuntimeError):
    """An identifier index could not be built or used."""


@dataclass(frozen=True)
class Detection:
    """The outcome of guessing which identifier type a list of values uses.

    Attributes:
        id_type: A supported identifier type, or ``"mixed"`` when the values do
            not agree on one.
        confidence: Share of values matching ``id_type``.
        warning: Anything the caller should be told about the guess.
    """

    id_type: str
    confidence: float
    warning: str | None = None


@dataclass(frozen=True)
class ResolutionReport:
    """Per-input resolution results plus the summary needed to decide on them.

    Attributes:
        frame: One row per input value, with columns ``input``, the primary key
            (named by ``key``), ``matched_by`` and ``note``. The key is null for
            anything not resolved, and input order is preserved.
        id_type: The identifier type actually used.
        detected: Whether ``id_type`` was guessed rather than specified.
        warnings: Anything the caller should see even on success.
    """

    frame: pl.DataFrame
    #: Name of the primary-key column in ``frame`` -- the database's own key
    #: name, so a report is readable without knowing how it was produced.
    key: str
    id_type: str
    detected: bool = False
    warnings: list[str] = dataclass_field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        """How many inputs landed in each ``matched_by`` category."""
        return dict(Counter(self.frame["matched_by"].to_list()))

    @property
    def n_input(self) -> int:
        """Number of input values."""
        return self.frame.height

    @property
    def n_resolved(self) -> int:
        """Number of inputs that reached a primary key."""
        return int(self.frame[self.key].is_not_null().sum())

    @property
    def unmatched(self) -> list[str]:
        """Inputs that matched nothing."""
        return self._inputs_where("unmatched")

    @property
    def ambiguous(self) -> list[str]:
        """Inputs that matched more than one gene."""
        return self._inputs_where("ambiguous")

    @property
    def ok(self) -> bool:
        """Whether every input resolved to exactly one gene."""
        return not self.unmatched and not self.ambiguous

    def resolved(self) -> pl.DataFrame:
        """Return only the rows that resolved, deduplicated on the primary key."""
        return self.frame.filter(pl.col(self.key).is_not_null()).unique(
            subset=[self.key],
            keep="first",
            maintain_order=True,
        )

    def _inputs_where(self, kind: str) -> list[str]:
        matches = self.frame.filter(pl.col("matched_by") == kind)["input"]
        return [str(value) for value in matches.to_list()]

    def describe(self, *, max_listed: int = 10) -> str:
        """Return a printable summary, listing problem inputs."""
        lines = [
            f"{self.n_resolved}/{self.n_input} resolved "
            f"as {self.id_type}{' (detected)' if self.detected else ''}",
        ]
        lines.extend(
            f"  {kind}: {count}"
            for kind, count in sorted(self.counts.items(), key=lambda kv: -kv[1])
        )
        for label, values in (
            ("unmatched", self.unmatched),
            ("ambiguous", self.ambiguous),
        ):
            if values:
                shown = ", ".join(values[:max_listed])
                more = (
                    f" (+{len(values) - max_listed} more)"
                    if len(values) > max_listed
                    else ""
                )
                lines.append(f"  which {label}: {shown}{more}")
        lines.extend(f"  warning: {w}" for w in self.warnings)
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.describe()


#: Warning attached whenever bare integers are taken to be NCBI IDs.
_BARE_INTEGER_WARNING = (
    "bare integers could be ncbi_id, hgnc_id or omim_id; assuming ncbi_id -- "
    "pass an explicit id_type to be sure"
)


def classify_id(value: str) -> ID_TYPE:
    """Return the identifier type a single value looks like.

    Falls back to ``symbol``, which is the only type without a recognisable
    shape. Bare integers become ``ncbi`` -- see :data:`_BARE_INTEGER_WARNING`.
    """
    text = str(value).strip()
    if _HGNC_RE.match(text):
        return "hgnc"
    if _ENSG_RE.match(text):
        return "ensg"
    if _INTEGER_RE.match(text):
        return "ncbi"
    return "symbol"


def detect_id_type(values: Iterable[str]) -> Detection:
    """Guess the identifier type a collection of values uses.

    Returns ``id_type="mixed"`` as soon as more than one recognisable shape is
    present, which :meth:`GeneIndex.resolve` handles by classifying each value on
    its own. A majority rule would be worse here: a list of symbols containing
    two Ensembl IDs would be resolved wholly as symbols, and those two would fail
    for no reason the user could see.
    """
    cleaned = [str(v).strip() for v in values if str(v).strip()]
    if not cleaned:
        return Detection(
            id_type="symbol",
            confidence=0.0,
            warning="no values to inspect",
        )

    counts = Counter(classify_id(v) for v in cleaned)
    best, hits = counts.most_common(1)[0]
    share = hits / len(cleaned)

    if len(counts) > 1:
        return Detection(id_type="mixed", confidence=share)
    warning = _BARE_INTEGER_WARNING if best == "ncbi" else None
    return Detection(id_type=best, confidence=share, warning=warning)


def normalize(values: Iterable[str] | pl.Series, id_type: ID_TYPE) -> pl.Series:
    """Return values in the canonical form used for matching.

    Integer identifiers survive a spreadsheet round-trip (``"1.0"``) and an
    ``HGNC:``/``OMIM:`` prefix; Ensembl IDs lose any ``.12`` version suffix;
    symbols are upper-cased. Anything unparseable becomes null rather than
    raising, so a single bad row cannot fail a whole list.
    """
    series = (
        values if isinstance(values, pl.Series) else pl.Series("input", list(values))
    )
    text = series.cast(pl.String).str.strip_chars()

    if id_type in _INTEGER_IDS:
        return (
            text.str.replace(r"(?i)^(HGNC|OMIM|MIM|GeneID|ENTREZ):\s*", "")
            .cast(pl.Float64, strict=False)
            .cast(pl.Int64, strict=False)
            .alias("normalized")
        )
    if id_type == "ensg":
        return (
            text.str.to_uppercase()
            .str.replace(_VERSION_SUFFIX_RE, "")
            .alias("normalized")
        )
    if id_type == "symbol":
        return text.str.to_uppercase().alias("normalized")

    msg = f"cannot normalize identifier type {id_type!r}"
    raise GeneIndexError(msg)


class GeneIndex:
    """Maps identifiers of any supported type onto the database's primary key.

    Build one from a database frame, then optionally enrich it with the HGNC
    complete set so retired symbols resolve::

        index = GeneIndex.from_frame(db.frame).with_hgnc()
        report = index.resolve(["SHANK3", "KIAA1650"])
    """

    def __init__(self, frame: pl.DataFrame, roles: Roles | None = None) -> None:
        if roles is None:
            roles = Roles(
                primary_key=LEGACY_PRIMARY_KEY,
                symbol=LEGACY_ID_COLUMNS["symbol"],
                ensg=LEGACY_ID_COLUMNS["ensg"],
                ncbi=LEGACY_ID_COLUMNS["ncbi"],
                hgnc=LEGACY_ID_COLUMNS["hgnc"],
                omim=LEGACY_ID_COLUMNS["omim"],
            )
        self._roles = roles
        self._key = roles.primary_key
        if self._key is None:
            msg = "no field is marked primary_key, so nothing can be resolved onto"
            raise GeneIndexError(msg)

        required = [self._key] + ([roles.symbol] if roles.symbol else [])
        if missing := [c for c in required if c not in frame.columns]:
            msg = f"frame is missing required identifier column(s): {missing}"
            raise GeneIndexError(msg)

        self._frame = frame
        # Kept at the database's own dtype: a key may be an integer (ncbi_id) or
        # a string (hgnc_id, "HGNC:5"). Casting would corrupt the latter.
        self._targets = frame[self._key]
        self._lookups: dict[str, pl.DataFrame] = {}
        self._hgnc: pl.DataFrame | None = None
        self._hgnc_path: Path | None = None
        self._alias_lookup: pl.DataFrame | None = None
        self._alias_meta: dict[str, str] = {}

    @classmethod
    def from_frame(cls, frame: pl.DataFrame, roles: Roles | None = None) -> GeneIndex:
        """Build an index from a database frame."""
        return cls(frame, roles)

    @property
    def key(self) -> str:
        """The primary-key column every identifier resolves onto."""
        assert self._key is not None  # noqa: S101 - established in __init__
        return self._key

    @property
    def key_symbol(self) -> str:
        """The symbol column, which resolution always needs."""
        assert self._roles.symbol is not None  # noqa: S101 - required in __init__
        return self._roles.symbol

    def _column_for(self, id_type: str) -> str | None:
        """The database column backing an identifier type, or None."""
        return getattr(self._roles, id_type, None)

    @property
    def hgnc_path(self) -> Path | None:
        """Path of the loaded HGNC complete set, if any."""
        return self._hgnc_path

    @property
    def alias_index_meta(self) -> dict[str, str]:
        """Provenance of the loaded alias index, empty if none is loaded."""
        return dict(self._alias_meta)

    @property
    def symbol_coverage(self) -> str:
        """How far symbol resolution reaches, as one of three states.

        ``"hgnc"`` when the complete set itself is loaded, ``"alias-index"``
        when the precomputed distillation of it is, and ``"exact-only"`` when
        neither is -- in which case a retired symbol resolves to nothing. The
        container ships the index and not the complete set, so a caller needs
        to be able to tell these apart rather than infer them.
        """
        if self._hgnc is not None:
            return "hgnc"
        if self._alias_lookup is not None:
            return "alias-index"
        return "exact-only"

    @property
    def available_id_types(self) -> list[str]:
        """Identifier types this index can resolve, given the frame's columns."""
        return [
            t
            for t in LEGACY_ID_COLUMNS
            if (col := self._column_for(t)) and col in self._frame.columns
        ]

    # ------------------------------------------------------------ HGNC loading

    def with_hgnc(self, path: Path | str | None = None) -> GeneIndex:
        """Load the HGNC complete set to enable alias and previous-symbol matching.

        Args:
            path: The complete-set file. Defaults to the newest
                ``hgnc_complete_set*`` in the resources tree.

        Returns:
            This index, so calls can be chained.

        Raises:
            GeneIndexError: If no file is given and none can be found, or if the
                file lacks the columns needed.
        """
        resolved = Path(path) if path is not None else paths.latest_hgnc_file()
        if resolved is None:
            msg = (
                "no HGNC complete set found in the resources tree; pass an "
                "explicit path or download one to resources/original_file/"
            )
            raise GeneIndexError(msg)

        raw = pl.read_csv(
            resolved,
            separator="\t",
            infer_schema_length=0,
            truncate_ragged_lines=True,
        )
        absent = [c for c in _HGNC_COLUMNS if c not in raw.columns]
        if absent:
            msg = f"{resolved.name} is missing HGNC column(s): {absent}"
            raise GeneIndexError(msg)

        self._hgnc = raw.select(_HGNC_COLUMNS)
        self._hgnc_path = resolved
        # Invalidate the symbol lookup so it is rebuilt with alias coverage.
        self._lookups.pop("symbol", None)
        return self

    # ----------------------------------------------------- alias index (built)

    def symbol_lookup(self) -> pl.DataFrame:
        """The symbol table resolution uses, alias layers included.

        Four columns: ``normalized``, the primary key, ``matched_by`` and
        ``note``. Public so it can be serialised -- with the HGNC complete set
        loaded this *is* the alias index, which is why building the file needs
        no separate assembly logic.
        """
        return self._lookup_for("symbol")

    def write_alias_index(self, path: Path | str) -> int:
        """Write the symbol table to ``path`` and return its row count.

        Raises:
            GeneIndexError: If no alias source is loaded, which would write a
                file holding nothing but the exact symbols already in the
                parquet -- worse than no file, because it looks like coverage.
        """
        if self._hgnc is None:
            msg = (
                "no HGNC complete set loaded, so there are no aliases to "
                "write; call with_hgnc() first"
            )
            raise GeneIndexError(msg)
        frame = self.symbol_lookup()
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(
            destination,
            metadata=self._alias_index_metadata(frame),
        )
        return frame.height

    def _alias_index_metadata(self, frame: pl.DataFrame) -> dict[str, str]:
        """Provenance stamped into the file, each key answering one question.

        ``key_column`` is the load-bearing one: an index built against
        ``ncbi_id`` and loaded by a database keyed on ``hgnc_id`` would resolve
        every symbol to nothing, silently, so the mismatch is refused on load.
        ``frame_rows`` is what makes staleness detectable after the parquet is
        rebuilt with a different gene set.
        """
        source = self._hgnc_path
        return {
            "genetrek_alias_format": "1",
            "key_column": self.key,
            "symbol_column": self.key_symbol,
            "source_file": source.name if source else "",
            "frame_rows": str(self._frame.height),
            "index_rows": str(frame.height),
            "built": datetime.now(tz=UTC).strftime("%Y-%m-%d"),
        }

    def with_alias_index(self, path: Path | str | None = None) -> GeneIndex:
        """Load a precomputed alias index so retired symbols resolve.

        The file is used as the symbol table verbatim: it already carries the
        exact-symbol layer under the alias and previous-symbol ones, in the
        order :meth:`_symbol_lookup` would have produced.

        A loaded HGNC complete set still wins -- it is the authority the file
        was distilled from.

        Args:
            path: The index file. Defaults to the resources tree's own.

        Returns:
            This index, so calls can be chained.

        Raises:
            GeneIndexError: If the file is absent, malformed, or was built
                against a different primary key.
        """
        resolved = Path(path) if path is not None else paths.aliases_path()
        if not resolved.is_file():
            msg = f"no alias index at {resolved}"
            raise GeneIndexError(msg)

        frame = pl.read_parquet(resolved)
        expected = ("normalized", self.key, "matched_by", "note")
        if missing := [c for c in expected if c not in frame.columns]:
            msg = (
                f"{resolved.name} is not an alias index for this database: "
                f"missing column(s) {missing}. It was probably built against a "
                f"database with a different primary key."
            )
            raise GeneIndexError(msg)

        meta = pl.read_parquet_metadata(resolved)
        if (stored := meta.get("key_column")) and stored != self.key:
            msg = (
                f"{resolved.name} was built against {stored!r} but this "
                f"database keys on {self.key!r}; rebuild it with "
                f"`genetrek-db build-aliases`"
            )
            raise GeneIndexError(msg)

        self._alias_lookup = frame.select(*expected)
        self._alias_meta = meta
        # Invalidate the symbol lookup so it is rebuilt with alias coverage.
        self._lookups.pop("symbol", None)
        return self

    # -------------------------------------------------------------- resolution

    def resolve(
        self,
        values: Sequence[str] | pl.Series,
        id_type: ID_TYPE | str = "auto",
    ) -> ResolutionReport:
        """Resolve identifiers onto the primary key.

        Args:
            values: Identifiers to resolve. Order is preserved in the report.
            id_type: One of ``ncbi``, ``ensg``, ``symbol``, ``hgnc``, ``omim``;
                ``"mixed"`` to classify each value on its own; or ``"auto"``
                (the default) to use a single detected type when the values
                agree and fall back to ``"mixed"`` when they do not.

        Returns:
            A :class:`ResolutionReport`. Inputs that match nothing get a null
            primary key and ``matched_by == "unmatched"``; inputs matching more
            than one gene get ``"ambiguous"`` -- neither raises, so the caller
            decides what is acceptable.

        Raises:
            GeneIndexError: If ``id_type`` is unknown or unsupported by the frame.
        """
        raw = [
            str(v)
            for v in (
                values.to_list() if isinstance(values, pl.Series) else list(values)
            )
        ]
        warnings: list[str] = []
        detected = id_type == "auto"

        if detected:
            detection = detect_id_type(raw)
            requested = detection.id_type
            if detection.warning:
                warnings.append(detection.warning)
        else:
            requested = str(id_type)

        if requested == "mixed":
            frame = self._resolve_mixed(raw, warnings)
        else:
            self._require_id_type(requested)
            frame = self._resolve_single(raw, requested, warnings)

        return ResolutionReport(
            frame=frame,
            key=self.key,
            id_type=requested,
            detected=detected,
            warnings=warnings,
        )

    def _require_id_type(self, id_type: str) -> None:
        if id_type not in LEGACY_ID_COLUMNS:
            msg = (
                f"unknown id_type {id_type!r}; expected one of "
                f"{sorted(LEGACY_ID_COLUMNS)}, 'mixed' or 'auto'"
            )
            raise GeneIndexError(msg)
        column = self._column_for(id_type)
        if column is None:
            msg = f"cannot resolve by {id_type!r}: no field declares that id_type"
            raise GeneIndexError(msg)
        if column not in self._frame.columns:
            msg = (
                f"cannot resolve by {id_type!r}: the database has no {column!r} column"
            )
            raise GeneIndexError(msg)

    def _resolve_single(
        self,
        raw: list[str],
        id_type: str,
        warnings: list[str],
    ) -> pl.DataFrame:
        if id_type == "symbol" and self.symbol_coverage == "exact-only":
            warnings.append(
                "no HGNC complete set or alias index loaded, so retired "
                "symbols will not resolve; run `genetrek-db build-aliases` or "
                "call with_hgnc() to enable alias matching",
            )
        # Dtypes are pinned so an empty list still joins instead of producing a
        # Null-typed key column.
        series = pl.Series("input", raw, dtype=pl.String)
        inputs = pl.DataFrame({"input": series}).with_columns(
            normalize(series, id_type),  # type: ignore[arg-type]
        )
        matched = inputs.join(self._lookup_for(id_type), on="normalized", how="left")
        return matched.select(
            "input",
            pl.col(self.key),
            pl.col("matched_by").fill_null("unmatched"),
            pl.col("note").fill_null(""),
        )

    def _resolve_mixed(self, raw: list[str], warnings: list[str]) -> pl.DataFrame:
        """Classify each value on its own, then resolve per type and reassemble.

        Order is preserved by carrying the original position through the split.
        """
        classified = [classify_id(v) for v in raw]
        if "ncbi" in classified:
            warnings.append(_BARE_INTEGER_WARNING)

        parts: list[pl.DataFrame] = []
        for id_type in dict.fromkeys(classified):
            self._require_id_type(id_type)
            positions = [i for i, t in enumerate(classified) if t == id_type]
            part = self._resolve_single(
                [raw[i] for i in positions],
                id_type,
                warnings,
            )
            parts.append(
                part.with_columns(pl.Series("_pos", positions, dtype=pl.Int64))
            )

        columns = ("input", self.key, *_REPORT_TAIL)
        if not parts:
            return pl.DataFrame(
                {
                    "input": pl.Series([], dtype=pl.String),
                    self.key: pl.Series([], dtype=self._targets.dtype),
                    "matched_by": pl.Series([], dtype=pl.String),
                    "note": pl.Series([], dtype=pl.String),
                },
            )
        return (
            pl.concat(parts, how="vertical").sort("_pos").drop("_pos").select(*columns)
        )

    # ------------------------------------------------------------------ lookups

    def _lookup_for(self, id_type: str) -> pl.DataFrame:
        """Return (and cache) the normalized value -> primary key table."""
        if id_type not in self._lookups:
            self._lookups[id_type] = (
                self._symbol_lookup()
                if id_type == "symbol"
                else self._direct_lookup(id_type)
            )
        return self._lookups[id_type]

    def _direct_lookup(self, id_type: str) -> pl.DataFrame:
        column = self._column_for(id_type)
        if column is None:
            msg = f"no field declares id_type {id_type!r}"
            raise GeneIndexError(msg)
        pairs = pl.DataFrame(
            {
                "normalized": normalize(self._frame[column], id_type),  # type: ignore[arg-type]
                self.key: self._targets,
            },
        ).drop_nulls("normalized")
        return _collapse(pairs, self.key, matched_by=column)

    def _symbol_lookup(self) -> pl.DataFrame:
        """Build the symbol table, layering HGNC aliases under exact matches."""
        # The precomputed index is already the finished table, so it short-
        # circuits the layering rather than being another layer in it.
        if self._hgnc is None and self._alias_lookup is not None:
            return self._alias_lookup
        exact = _collapse(
            pl.DataFrame(
                {
                    "normalized": normalize(self._frame[self.key_symbol], "symbol"),
                    self.key: self._targets,
                },
            ).drop_nulls("normalized"),
            self.key,
            matched_by="symbol",
        )
        if self._hgnc is None:
            return exact

        bridged = self._bridge_hgnc(self._hgnc)
        alias_layers = [
            _collapse(pairs, self.key, matched_by=kind)
            for kind, pairs in self._hgnc_symbol_pairs(bridged).items()
            if pairs.height
        ]
        if not alias_layers:
            return exact

        # Exact matches win; each alias layer only fills what is still missing.
        combined = exact
        for layer in alias_layers:
            fresh = layer.join(
                combined.select("normalized"), on="normalized", how="anti"
            )
            combined = pl.concat([combined, fresh], how="vertical")
        return combined

    def _hgnc_symbol_pairs(self, bridged: pl.DataFrame) -> dict[str, pl.DataFrame]:
        """Explode HGNC's multi-value symbol fields into value -> key pairs."""
        key = self.key
        out: dict[str, pl.DataFrame] = {}
        for column in ("alias_symbol", "prev_symbol"):
            out[column] = (
                bridged.select(
                    pl.col(column)
                    .fill_null("")
                    .str.split(_HGNC_MULTI_SEP)
                    .alias("raw"),
                    pl.col("symbol").alias("approved"),
                    pl.col(key),
                )
                .explode("raw")
                .with_columns(pl.col("raw").str.strip_chars().str.to_uppercase())
                .filter(pl.col("raw") != "")
                .select(
                    pl.col("raw").alias("normalized"),
                    pl.col(key),
                    pl.col("approved"),
                )
            )
        return out

    def _bridge_hgnc(self, hgnc: pl.DataFrame) -> pl.DataFrame:
        """Attach a primary key to each HGNC row, cascading three bridges.

        Entrez IDs, HGNC IDs and symbols each reach a different subset of the
        database -- against the real files, roughly 43.3k, 42.7k and 41.6k of
        HGNC's 44.1k rows -- so cascading them resolves materially more than any
        one bridge alone. Returns only the rows that reached a key.
        """
        key = self.key
        keyed = hgnc.with_columns(
            normalize(hgnc["entrez_id"], "ncbi").alias("_hgnc_ncbi"),
            normalize(hgnc["hgnc_id"], "hgnc").alias("_hgnc_hgnc"),
            normalize(hgnc["symbol"], "symbol").alias("_hgnc_symbol"),
        )

        # One bridge per identifier the database actually carries, tried in order
        # of reliability. Each maps an HGNC row to a primary key without assuming
        # anything about that key's dtype.
        bridges: list[str] = []
        for role, hgnc_col in (
            ("ncbi", "_hgnc_ncbi"),
            ("hgnc", "_hgnc_hgnc"),
            ("symbol", "_hgnc_symbol"),
        ):
            column = self._column_for(role)
            if column is None or column not in self._frame.columns:
                continue
            via = f"_via_{role}"
            keyed = keyed.join(
                _unique_pairs(
                    normalize(self._frame[column], role),  # type: ignore[arg-type]
                    self._targets,
                    key=hgnc_col,
                    value=via,
                ),
                on=hgnc_col,
                how="left",
            )
            bridges.append(via)

        if not bridges:
            return keyed.head(0).with_columns(
                pl.lit(None).cast(self._targets.dtype).alias(key),
            )
        return keyed.with_columns(pl.coalesce(bridges).alias(key)).drop_nulls(key)

    def __repr__(self) -> str:
        hgnc = self._hgnc_path.name if self._hgnc_path else "not loaded"
        return (
            f"<GeneIndex {self._frame.height:,} genes, "
            f"types={self.available_id_types}, hgnc={hgnc}>"
        )


def _collapse(pairs: pl.DataFrame, key: str, *, matched_by: str) -> pl.DataFrame:
    """Reduce value -> key pairs to one row per value, flagging ambiguity.

    A value mapping to several distinct genes cannot be resolved, so it is kept
    with a null key and marked ``ambiguous`` rather than silently taking one.
    """
    has_approved = "approved" in pairs.columns
    grouped = pairs.group_by("normalized").agg(
        pl.col(key).unique().alias("_keys"),
        *([pl.col("approved").first().alias("_approved")] if has_approved else []),
    )
    note = (
        pl.when(pl.col("_keys").list.len() > 1)
        .then(
            pl.format("{} candidate genes", pl.col("_keys").list.len()),
        )
        .otherwise(
            pl.format("renamed -> {}", pl.col("_approved"))
            if has_approved
            else pl.lit(""),
        )
    )
    return grouped.select(
        "normalized",
        pl.when(pl.col("_keys").list.len() == 1)
        .then(pl.col("_keys").list.first())
        .otherwise(None)
        .alias(key),
        pl.when(pl.col("_keys").list.len() > 1)
        .then(pl.lit("ambiguous"))
        .otherwise(pl.lit(matched_by))
        .alias("matched_by"),
        note.alias("note"),
    )


def _unique_pairs(
    keys: pl.Series,
    targets: pl.Series,
    *,
    key: str,
    value: str,
) -> pl.DataFrame:
    """Return a key -> primary-key table, dropping keys used by several genes.

    An identifier shared by more than one gene cannot bridge anything, so it is
    dropped rather than resolved arbitrarily.
    """
    return (
        pl.DataFrame({key: keys, value: targets})
        .drop_nulls(key)
        .group_by(key)
        .agg(pl.col(value).unique().alias("_v"))
        .filter(pl.col("_v").list.len() == 1)
        .select(key, pl.col("_v").list.first().alias(value))
    )
